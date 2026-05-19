#!/usr/bin/env node
/**
 * pipeline/crawl.mjs — scholarly discovery + full-text fetch for the
 * Opus scout team. NO API keys, NO OpenRouter. Structured search via
 * keyless scholarly APIs (OpenAlex primary, Europe PMC, arXiv, Semantic
 * Scholar) + real full-text retrieval (Europe PMC OA XML → Playwright
 * HTML/PDF). Output is JSON on stdout; diagnostics on stderr.
 *
 * Usage:
 *   node pipeline/crawl.mjs search  --query "<q>" --since-journal YYYY-MM-DD \
 *        --since-preprint YYYY-MM-DD [--limit 30]
 *   node pipeline/crawl.mjs fulltext --doi 10.x [--url https://...] [--pdf https://...]
 *
 * Designed to be called repeatedly by an Opus scout agent that formulates
 * its own domain-specific queries and judges relevance from full text.
 */
import { chromium } from "playwright";

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36";
const MAILTO = "csnl@vnilab.local"; // OpenAlex polite pool
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function arg(name, def = null) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}
function normDoi(d) {
  if (!d) return null;
  return String(d)
    .replace(/^https?:\/\/(dx\.)?doi\.org\//i, "")
    .trim()
    .toLowerCase() || null;
}
async function jget(url, { tries = 3, headers = {}, timeoutMs = 25000 } = {}) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, {
        headers: { "User-Agent": UA, Accept: "application/json", ...headers },
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (r.status === 429) {
        await sleep(2000 * (i + 1) + 1000);
        continue;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const ct = r.headers.get("content-type") || "";
      return ct.includes("json") ? await r.json() : await r.text();
    } catch (e) {
      if (i === tries - 1) {
        process.stderr.write(`[jget] ${url} :: ${e.message}\n`);
        return null;
      }
      await sleep(1200 * (i + 1));
    }
  }
  return null;
}

// ---- abstract reconstruction for OpenAlex inverted index ------------------
function invToAbstract(inv) {
  if (!inv) return null;
  const out = [];
  for (const [w, ps] of Object.entries(inv)) for (const p of ps) out[p] = w;
  return out.join(" ").replace(/\s+/g, " ").trim() || null;
}

// ---------------------------- SEARCH SOURCES -------------------------------
async function srcOpenAlex(q, sinceJournal, limit) {
  // Precision-first: phrase match in title+abstract, ranked by OpenAlex
  // relevance (default sort) — NOT date-desc (which surfaces recent noise).
  const u =
    `https://api.openalex.org/works?filter=` +
    `title_and_abstract.search:${encodeURIComponent(q)},` +
    `from_publication_date:${sinceJournal}` +
    `&per-page=${Math.min(limit, 50)}&mailto=${MAILTO}`;
  const j = await jget(u);
  if (!j || !j.results) return [];
  return j.results.map((w) => {
    const loc = w.primary_location || {};
    const oa = w.best_oa_location || {};
    const isPre =
      (w.type || "").includes("preprint") ||
      ((loc.source && (loc.source.type || "")) === "repository") ||
      /biorxiv|medrxiv|arxiv|ssrn|openrxiv|preprint/i.test(
        (loc.source && loc.source.display_name) || ""
      );
    return {
      title: w.title,
      doi: normDoi(w.doi),
      url: loc.landing_page_url || (w.doi || null),
      pdf_url: oa.pdf_url || loc.pdf_url || null,
      authors: (w.authorships || []).map((a) => a.author && a.author.display_name).filter(Boolean),
      venue: (loc.source && loc.source.display_name) || w.host_venue?.display_name || null,
      date: w.publication_date || null,
      is_preprint: !!isPre,
      abstract: invToAbstract(w.abstract_inverted_index),
      source: "openalex",
    };
  });
}
async function srcEuropePMC(q, since, limit) {
  const u =
    `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=` +
    encodeURIComponent(`${q} AND (FIRST_PDATE:[${since} TO 3000-01-01])`) +
    `&format=json&pageSize=${Math.min(limit, 40)}&resultType=core`;
  const j = await jget(u);
  const rs = (j && j.resultList && j.resultList.result) || [];
  return rs.map((r) => ({
    title: r.title,
    doi: normDoi(r.doi),
    url: r.doi ? `https://doi.org/${r.doi}` : (r.fullTextUrlList?.fullTextUrl?.[0]?.url || null),
    pdf_url: (r.fullTextUrlList?.fullTextUrl || []).find((f) => f.documentStyle === "pdf")?.url || null,
    authors: (r.authorString || "").split(", ").filter(Boolean),
    venue: r.journalInfo?.journal?.title || r.bookOrReportDetails?.publisher || r.source || null,
    date: r.firstPublicationDate || null,
    is_preprint: (r.source || "") === "PPR" || /preprint/i.test(r.pubTypeList?.pubType?.join(" ") || ""),
    abstract: r.abstractText || null,
    source: "europepmc",
    _pmcid: r.pmcid || null,
    _epmc: r.source && r.id ? { src: r.source, id: r.id } : null,
  }));
}
async function srcArxiv(q, limit) {
  const u =
    `http://export.arxiv.org/api/query?search_query=all:${encodeURIComponent(q)}` +
    `&sortBy=relevance&max_results=${Math.min(limit, 20)}`;
  const x = await jget(u, {
    headers: { Accept: "application/atom+xml" },
    tries: 2,
    timeoutMs: 38000,
  });
  if (!x || typeof x !== "string") return [];
  const out = [];
  for (const m of x.matchAll(/<entry>([\s\S]*?)<\/entry>/g)) {
    const e = m[1];
    const g = (re) => (e.match(re) || [, null])[1];
    const id = g(/<id>([\s\S]*?)<\/id>/);
    const doi = g(/<arxiv:doi[^>]*>([\s\S]*?)<\/arxiv:doi>/);
    out.push({
      title: (g(/<title>([\s\S]*?)<\/title>/) || "").replace(/\s+/g, " ").trim(),
      doi: normDoi(doi) || (id ? `arxiv:${id.split("/abs/")[1]}` : null),
      url: id,
      pdf_url: id ? id.replace("/abs/", "/pdf/") : null,
      authors: [...e.matchAll(/<name>([\s\S]*?)<\/name>/g)].map((a) => a[1].trim()),
      venue: "arXiv",
      date: (g(/<published>([\s\S]*?)<\/published>/) || "").slice(0, 10),
      is_preprint: true,
      abstract: (g(/<summary>([\s\S]*?)<\/summary>/) || "").replace(/\s+/g, " ").trim(),
      source: "arxiv",
    });
  }
  return out;
}
async function srcS2(q, limit) {
  const u =
    `https://api.semanticscholar.org/graph/v1/paper/search?query=${encodeURIComponent(q)}` +
    `&fields=title,abstract,year,venue,publicationDate,externalIds,openAccessPdf,publicationTypes` +
    `&limit=${Math.min(limit, 25)}`;
  const j = await jget(u, { tries: 2 });
  const rs = (j && j.data) || [];
  return rs.map((p) => ({
    title: p.title,
    doi: normDoi(p.externalIds && p.externalIds.DOI),
    url: p.externalIds?.DOI ? `https://doi.org/${p.externalIds.DOI}` : null,
    pdf_url: p.openAccessPdf && p.openAccessPdf.url,
    authors: [],
    venue: p.venue || null,
    date: p.publicationDate || (p.year ? `${p.year}-01-01` : null),
    is_preprint: /preprint|posted/i.test((p.publicationTypes || []).join(" ")),
    abstract: p.abstract || null,
    source: "s2",
  }));
}

function withinWindow(rec, sinceJournal, sincePreprint) {
  if (!rec.date) return false;
  const cut = rec.is_preprint ? sincePreprint : sinceJournal;
  return rec.date >= cut;
}
function dedupe(list) {
  const seen = new Map();
  for (const r of list) {
    if (!r.title) continue;
    const k = r.doi || r.title.toLowerCase().replace(/\s+/g, " ").trim().slice(0, 80);
    const prev = seen.get(k);
    if (!prev) seen.set(k, r);
    else if ((r.abstract || "").length > (prev.abstract || "").length) seen.set(k, r);
  }
  return [...seen.values()];
}

async function cmdSearch() {
  const q = arg("query");
  const sj = arg("since-journal");
  const sp = arg("since-preprint") || sj;
  const limit = parseInt(arg("limit", "30"), 10);
  if (!q || !sj) {
    process.stderr.write("search needs --query and --since-journal\n");
    process.exit(2);
  }
  const [a, b, c, d] = await Promise.all([
    srcOpenAlex(q, sj, limit),
    srcEuropePMC(q, sj, limit),
    srcArxiv(q, limit),
    srcS2(q, limit),
  ]);
  // Round-robin interleave preserves each source's RELEVANCE order
  // (OpenAlex/S2/EPMC/arXiv all return relevance-ranked); no global
  // date sort (that buried relevant in-window papers under recent noise).
  const lanes = [a, b, c, d];
  const merged = [];
  for (let i = 0; i < Math.max(...lanes.map((l) => l.length)); i++)
    for (const l of lanes) if (l[i]) merged.push(l[i]);
  const all = dedupe(merged).filter((r) => withinWindow(r, sj, sp));
  process.stdout.write(
    JSON.stringify(
      { query: q, since_journal: sj, since_preprint: sp, n: all.length, results: all.slice(0, limit) },
      null,
      2
    )
  );
}

// ---------------------------- FULL TEXT ------------------------------------
function xmlToText(xml) {
  return xml
    .replace(/<ref-list[\s\S]*?<\/ref-list>/gi, "")
    .replace(/<back[\s\S]*?<\/back>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&[a-z]+;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
async function epmcFullText(doi) {
  if (!doi) return null;
  const s = await jget(
    `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:${encodeURIComponent(
      doi
    )}&format=json&resultType=lite`
  );
  const r = s && s.resultList && s.resultList.result && s.resultList.result[0];
  if (!r || !r.pmcid || r.isOpenAccess !== "Y") return null;
  const xml = await jget(
    `https://www.ebi.ac.uk/europepmc/webservices/rest/PMC/${r.pmcid}/fullTextXML`,
    { headers: { Accept: "application/xml" } }
  );
  if (!xml || typeof xml !== "string" || xml.length < 1200) return null;
  const t = xmlToText(xml);
  return t.length > 1500 ? t : null;
}
async function pdfText(buf) {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const doc = await pdfjs.getDocument({ data: new Uint8Array(buf), useSystemFonts: true }).promise;
  let out = "";
  for (let i = 1; i <= Math.min(doc.numPages, 40); i++) {
    const pg = await doc.getPage(i);
    const tc = await pg.getTextContent();
    out += tc.items.map((it) => it.str).join(" ") + "\n";
  }
  return out.replace(/\s+/g, " ").trim();
}
async function browserFullText(url, pdfUrl) {
  const browser = await chromium.launch({ headless: true });
  try {
    const ctx = await browser.newContext({ userAgent: UA });
    // PDF path
    const tryPdf = pdfUrl || (url && /\.pdf($|\?)/i.test(url) ? url : null);
    if (tryPdf) {
      try {
        const resp = await ctx.request.get(tryPdf, { timeout: 30000 });
        if (resp.ok()) {
          const t = await pdfText(await resp.body());
          if (t && t.length > 1500) return { mode: "pdf", text: t };
        }
      } catch (e) {
        process.stderr.write(`[pdf] ${e.message}\n`);
      }
    }
    if (!url) return null;
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 35000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const text = await page
      .evaluate(() => {
        const pick = ["article", '[role="main"]', "main", ".article-fulltext",
          ".fulltext-view", "#bodymatter", ".c-article-body", "#content"];
        let el = null;
        for (const s of pick) { el = document.querySelector(s); if (el && el.innerText && el.innerText.length > 1200) break; el = null; }
        const root = el || document.body;
        root.querySelectorAll("nav,header,footer,script,style,.references,#references,[role=navigation]").forEach((n) => n.remove());
        return (root.innerText || "").replace(/\s+/g, " ").trim();
      })
      .catch(() => "");
    if (text && text.length > 1500) return { mode: "html", text };
    return null;
  } finally {
    await browser.close();
  }
}
async function cmdFullText() {
  const doi = normDoi(arg("doi"));
  const url = arg("url");
  const pdf = arg("pdf");
  let r = null;
  try { r = await epmcFullText(doi); } catch (e) { process.stderr.write(`[epmc] ${e.message}\n`); }
  if (r) return process.stdout.write(JSON.stringify({ doi, mode: "epmc_xml", chars: r.length, text: r }));
  let br = null;
  try { br = await browserFullText(url, pdf); } catch (e) { process.stderr.write(`[browser] ${e.message}\n`); }
  if (br) return process.stdout.write(JSON.stringify({ doi, url, mode: br.mode, chars: br.text.length, text: br.text }));
  process.stdout.write(JSON.stringify({ doi, url, mode: "unavailable", chars: 0, text: null }));
}

const cmd = process.argv[2];
if (cmd === "search") await cmdSearch();
else if (cmd === "fulltext") await cmdFullText();
else {
  process.stderr.write("usage: crawl.mjs search|fulltext  (see header)\n");
  process.exit(2);
}
