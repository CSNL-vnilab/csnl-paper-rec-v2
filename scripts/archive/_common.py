"""
scripts/archive/_common.py — shared utilities for the P13 archive layer.

All paths here are intentionally side-effect-free except for path resolution;
the ingest scripts decide when to write JSONL or push to the DB.

The csnl-paper-rec policy applies: NO LLM keys, NO Anthropic/OpenRouter calls,
NO Slack writes. Network use is restricted to keyless scholarly APIs through
`pipeline/crawl.mjs` (already vetted) — the Python side only does file I/O,
regex, and (operator-run) Postgres writes through pipeline/_db.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Make pipeline/_db.py importable from the archive scripts.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

KST = timezone(timedelta(hours=9))

# Optional fuzzy-match dependency (discovery/dedup only — see
# requirements-discovery.txt). Import-guarded so the core attended path and
# the JSONL helpers keep working without it; same_work() degrades to a
# conservative NO-merge when it is absent so a missing dep can never
# false-collapse two distinct works.
try:  # pragma: no cover - trivial import guard
    from rapidfuzz.fuzz import token_set_ratio as _token_set_ratio
except Exception:  # pragma: no cover - optional dep absent
    _token_set_ratio = None


# ----------------------------------------------------------------- helpers

def kst_iso() -> str:
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")


_DOI_RE = re.compile(r"\b10\.\d{3,}\/[^\s\"<>()]+", re.IGNORECASE)


def norm_doi(raw: Optional[str]) -> Optional[str]:
    """Normalise a DOI (or synthetic id): strip the DOI URL prefix and trailing
    punctuation, then lowercase.

    **PK-STABLE (MF-C):** this feeds canonical_id(), the archive_papers primary
    key, so its output must not drift. A leading 'doi:' prefix is intentionally
    NOT stripped here — doing so would shift the PK for any 'doi:'-prefixed row
    and orphan it (and its canonical_id-keyed archive_responses). The
    comparison-only 'doi:' strip lives in _cmp_doi() and is used by same_work /
    dedup only, never by canonical_id.

    A synthetic arXiv id ('arxiv:<id>') is preserved (lowercased). Returns None
    for empty input.
    """
    if not raw:
        return None
    s = str(raw).strip()
    s = re.sub(r"^https?:\/\/(dx\.)?doi\.org\/", "", s, flags=re.IGNORECASE)
    s = s.strip().rstrip(".").rstrip(",").rstrip(";").rstrip(")")
    return s.lower() or None


def _cmp_doi(raw: Optional[str]) -> Optional[str]:
    """Comparison-only DOI form for same-work dedup: norm_doi() then strip a
    leading 'doi:' so two string forms of one DOI ('doi:10.x' vs '10.x')
    compare equal. NOT used by canonical_id() — keeping the strip out of
    norm_doi() preserves canonical_id PK stability (MF-C)."""
    nd = norm_doi(raw)
    if nd is None:
        return None
    return re.sub(r"^doi:\s*", "", nd) or None


def is_arxiv_synthetic(doi: Optional[str]) -> bool:
    """True iff the normalised id is a synthetic arXiv key 'arxiv:<id>'.

    Synthetic keys are minted for preprints that have no real DOI; a paper
    carrying one is treated as *DOI-less* for same_work() purposes (it can
    only merge via a strong title match, never via DOI equality).
    """
    nd = norm_doi(doi)
    return bool(nd) and nd.startswith("arxiv:")


def extract_doi_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DOI_RE.search(text)
    if not m:
        return None
    return norm_doi(m.group(0))


def norm_title(t: Optional[str]) -> str:
    """Lowercased, punctuation-stripped form for fuzzy matching.

    Preserves CJK (Hangul, Han, Hiragana, Katakana) so two different
    Korean-titled papers in the same year do not collide to empty string
    and share a canonical_id. Diacritics on Latin characters are folded
    via NFKD (so 'Müller' → 'muller'); CJK code points pass through.
    """
    if not t:
        return ""
    # First, fold diacritics on Latin characters.
    folded = []
    for ch in unicodedata.normalize("NFKD", t):
        cp = ord(ch)
        # Keep CJK ranges (rough but adequate for lab use):
        #   AC00–D7A3 Hangul syllables
        #   1100–11FF Hangul Jamo
        #   3040–30FF Hiragana + Katakana
        #   4E00–9FFF CJK Unified Ideographs
        if (0xAC00 <= cp <= 0xD7A3 or 0x1100 <= cp <= 0x11FF
                or 0x3040 <= cp <= 0x30FF or 0x4E00 <= cp <= 0x9FFF):
            folded.append(ch)
        elif unicodedata.combining(ch):
            continue
        elif cp < 128:
            folded.append(ch)
        # else: drop other non-ASCII (rare for our archive)
    s = "".join(folded)
    # Strip ASCII punctuation but keep CJK alphanumerics.
    out = []
    for ch in s:
        cp = ord(ch)
        if ch.isalnum() or cp > 127:
            out.append(ch.lower() if cp < 128 else ch)
    return "".join(out)


def canonical_id(doi: Optional[str], title: Optional[str], year: Optional[int]) -> str:
    """Stable canonical hash. DOI wins; else norm(title)+'|'+year.

    The output is the **first 32 hex chars (128 bits) of sha256(key)** —
    truncated to keep table indices cheap. Collision probability over the
    lab archive (≤ 1e5 papers) is < 1e-29 per pair under the standard
    sha256-as-uniform-random model. Documented here and in
    state/schema_archive.sql so future operators don't assume a 64-hex
    digest.
    """
    if doi:
        key = "doi:" + (norm_doi(doi) or "")
    else:
        key = "ttl:" + norm_title(title) + "|" + (str(year) if year else "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------- same-work dedup (P33)
# Collapse a preprint<->published (or duplicate-source) *twin* while NEVER
# merging two genuinely distinct works. The invariant (see MF-6):
#   * two REAL DOIs => authoritative: equal-DOI merges, different-DOI never
#     merges (a shared/near title is not enough — real DOIs disagree => keep);
#   * a synthetic/None DOI on either side => the DOI cannot arbitrate, so fall
#     back to a STRONG fuzzy title match, and only when BOTH titles are
#     non-blank (a blank title is never a merge key).

def same_work(a: dict, b: dict, fuzz: int = 92) -> bool:
    """Deterministic, pure twin test over two {doi,title_norm} dicts.

    TRUE iff:
      * both sides carry a real (non-synthetic, non-None) DOI and those DOIs
        are equal after norm_doi; OR
      * at least one side has a synthetic/None DOI, both title_norm values are
        non-blank, and rapidfuzz.token_set_ratio(titles) >= `fuzz`.

    Two real-but-different DOIs => FALSE (never title-merge). A blank title on
    either side (when a DOI is missing/synthetic) => FALSE. If rapidfuzz is
    unavailable the title branch conservatively returns FALSE (no merge).
    """
    da = _cmp_doi(a.get("doi"))
    db = _cmp_doi(b.get("doi"))
    a_real = da is not None and not da.startswith("arxiv:")
    b_real = db is not None and not db.startswith("arxiv:")
    if a_real and b_real:
        # Real DOIs are authoritative: equal => same work, differ => distinct.
        return da == db
    # At least one side cannot be arbitrated by DOI -> require a strong title
    # match with both titles present.
    ta = (a.get("title_norm") or "").strip()
    tb = (b.get("title_norm") or "").strip()
    if not ta or not tb:
        return False
    if _token_set_ratio is None:
        return False
    return _token_set_ratio(ta, tb) >= fuzz


def dedup_same_work(records, composite_key: str = "composite") -> list:
    """Collapse provably-same-work records, keeping the max-`composite_key`
    representative of each group; return survivors in stable order (each group
    emitted at the position of its first-appearing member).

    Grouping: real-DOI-equal records are grouped first, then synthetic/None-DOI
    records with a non-blank title are pairwise-merged into any same_work()
    match (union-find, so twins chain transitively). Records with no reliable
    key at all — no real DOI AND a blank title_norm — are ALL kept (they can
    never be a merge key, so they never collapse).
    """
    records = list(records)
    n = len(records)
    if n <= 1:
        return records

    def _composite(r) -> float:
        try:
            return float(r.get(composite_key))
        except (TypeError, ValueError):
            return float("-inf")

    def _real_doi(r) -> Optional[str]:
        d = _cmp_doi(r.get("doi"))   # comparison form (MF-C: strips 'doi:' w/o touching PK)
        if d is None or d.startswith("arxiv:"):
            return None
        return d

    def _title(r) -> str:
        return (r.get("title_norm") or "").strip()

    # --- union-find over record indices -----------------------------------
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri == rj:
            return
        # Keep the smaller index as root for deterministic grouping.
        if ri < rj:
            parent[rj] = ri
        else:
            parent[ri] = rj

    # Pass 1: group by identical real DOI (authoritative, O(n)).
    by_doi: dict[str, int] = {}
    for i, r in enumerate(records):
        d = _real_doi(r)
        if d is None:
            continue
        if d in by_doi:
            union(i, by_doi[d])
        else:
            by_doi[d] = i

    # Each component carries AT MOST ONE distinct real DOI (Pass 1 grouped only
    # equal-DOI records). Track it so Pass 2 can never let a synthetic twin
    # bridge two different real DOIs into one group — the invariant "two
    # real-but-different DOIs never merge" must hold transitively, not just
    # pairwise (same_work() only sees a pair).
    comp_doi: dict[int, str] = {}
    for i in range(n):
        d = _real_doi(records[i])
        if d is not None:
            comp_doi[find(i)] = d

    # Pass 2: synthetic/None-DOI records with a real title merge into any
    # same_work() match, EXCEPT a merge that would put two distinct real DOIs
    # in the same component (forbidden — skip it).
    syn_idx = [i for i, r in enumerate(records)
               if _real_doi(r) is None and _title(r)]
    for a in syn_idx:
        for b in range(n):
            if b == a:
                continue
            ra, rb = find(a), find(b)
            if ra == rb or not same_work(records[a], records[b]):
                continue
            da_c, db_c = comp_doi.get(ra), comp_doi.get(rb)
            if da_c is not None and db_c is not None and da_c != db_c:
                continue  # would fuse two distinct real DOIs -> refuse
            union(a, b)
            merged = da_c if da_c is not None else db_c
            comp_doi.pop(ra, None)
            comp_doi.pop(rb, None)
            if merged is not None:
                comp_doi[find(a)] = merged

    # --- collapse: max-composite representative per group -----------------
    best: dict[int, int] = {}
    for i in range(n):
        root = find(i)
        if root not in best or _composite(records[i]) > _composite(records[best[root]]):
            best[root] = i

    out = []
    seen_roots: set[int] = set()
    for i in range(n):
        root = find(i)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        out.append(records[best[root]])
    return out


# ------------------------------------------------------------ record schema

@dataclass
class ArchivePaperRow:
    """Mirror of csnl_paper_rec.archive_papers (+ source attribution).

    Carries `_source` and `_source_ref` so the merge step can write
    archive_paper_sources alongside archive_papers without re-parsing.
    """
    canonical_id: str
    doi: Optional[str]
    title: Optional[str]
    title_norm: str
    authors_json: list = field(default_factory=list)
    venue: Optional[str] = None
    year: Optional[int] = None
    pub_date: Optional[str] = None
    is_preprint: bool = False
    abstract: Optional[str] = None
    page_count: Optional[int] = None
    pdf_path: Optional[str] = None
    # provenance (not persisted into archive_papers; goes into _sources)
    _source: str = ""
    _source_ref: str = ""
    _source_payload: dict = field(default_factory=dict)
    # always-set timestamps
    first_seen_at: str = field(default_factory=kst_iso)
    last_updated_at: str = field(default_factory=kst_iso)

    def to_dict(self) -> dict:
        return asdict(self)


# -------------------------------------------------------- JSONL convenience

def write_jsonl(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            if hasattr(r, "to_dict"):
                r = r.to_dict()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


# --------------------------------------------------- lab-scope keyword bag
# Drives the lab-relevance filter + tags. Synced to the PI-network categories
# (BDM, NN, fVC, VWM, SD, CG, METH) so a paper that hits any tag is in-scope.
# Korean terms included where they materially differ in the lab vocabulary.

LAB_SCOPE_TAGS: dict[str, tuple[str, ...]] = {
    "BDM": (
        "decision making", "perceptual decision", "evidence accumulation",
        "drift diffusion", "value-based", "subjective value", "bayesian inference",
        "prior", "posterior", "uncertainty", "confidence", "metacognition",
        "reaction time", "psychometric", "two-alternative forced choice",
        "신경경제학", "의사결정", "베이지안", "메타인지", "확신",
    ),
    "NN": (
        "recurrent neural network", "neural dynamics", "attractor",
        "population coding", "spiking neural", "biologically plausible",
        "Hopfield", "balanced network", "neural manifold",
        "신경망 동역학", "어트랙터", "스파이킹 신경망",
    ),
    "fVC": (
        "fMRI", "BOLD", "retinotopy", "primary visual cortex", "V1",
        "ocular dominance", "myelin map", "visual cortex", "voxelwise",
        "encoding model",
        "시각피질", "혈역학", "망막순응도", "fMRI 인코딩",
    ),
    "VWM": (
        "working memory", "visual working memory", "delay activity",
        "set size", "memory precision", "recall error", "mnemonic",
        "시각작업기억", "작업기억", "기억정밀도",
    ),
    "SD": (
        "serial dependence", "history bias", "history effect", "sequential bias",
        "attractive bias", "repulsive bias", "trial-by-trial",
        "시계열 의존성", "역사 효과",
    ),
    "CG": (
        "categorization", "category learning", "prototype", "generalization",
        "concept learning", "rule-based",
        "범주학습", "개념학습", "일반화",
    ),
    "METH": (
        "psychophysics", "EEG", "MEG", "pupillometry", "eye tracking",
        "psychometric function", "computational model", "model comparison",
        "hierarchical bayesian", "변분추론",
        "심리물리학", "동공측정", "시선추적", "계산모델", "위계적 베이지안",
    ),
}

# Venue keyword bag — papers in these journals are nearly always in-scope for
# the lab even if the title/abstract did not match a topic keyword.
LAB_SCOPE_VENUES: tuple[str, ...] = (
    "nature neuroscience", "nature human behaviour", "nature communications",
    "neuron", "current biology", "journal of neuroscience", "elife",
    "cerebral cortex", "cognition", "psychological science",
    "psychological review", "trends in cognitive sciences",
    "trends in neurosciences", "neuroimage", "plos biology",
    "plos computational biology", "biorxiv", "psyarxiv",
)


def lab_scope_match(text: str, venue: Optional[str] = None) -> list[str]:
    """Return scope tags the text matches (substring, case-insensitive).

    A venue match adds the synthetic tag "VENUE_OK" so the caller can
    detect 'paper looks lab-shaped from its venue alone' even when the
    abstract is missing or in a language the keyword bag does not cover.
    """
    if not text and not venue:
        return []
    hit: list[str] = []
    if text:
        low = text.lower()
        for tag, kws in LAB_SCOPE_TAGS.items():
            for k in kws:
                if k.lower() in low:
                    hit.append(tag)
                    break
    if venue:
        v = venue.lower()
        for vk in LAB_SCOPE_VENUES:
            if vk in v:
                hit.append("VENUE_OK")
                break
    # Deduplicate preserving order.
    seen = set()
    out = []
    for t in hit:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# -------------------------------------------------------- filename heuristic

_FILENAME_RE = re.compile(
    r"^(?P<author>[^_\d]{1,40}?)_(?P<year>(?:18|19|20)\d{2})_(?P<title>.+)\.pdf$",
    re.IGNORECASE,
)


def parse_filename(name: str) -> dict:
    """Best-effort Author_YYYY_Title.pdf parser. Robust to fragments.

    Returns dict with: author, year, title, looks_truncated. Title still has
    underscores; callers should replace _→space when displaying. Year is int
    when extracted, else None.
    """
    out = {"author": None, "year": None, "title": None, "looks_truncated": False}
    base = name.rsplit("/", 1)[-1]
    stem = base[:-4] if base.lower().endswith(".pdf") else base

    m = _FILENAME_RE.match(base)
    if m:
        out["author"] = m.group("author").strip()
        try:
            out["year"] = int(m.group("year"))
        except ValueError:
            out["year"] = None
        out["title"] = m.group("title").replace("_", " ").strip()
    else:
        # Fallback: filename is probably a truncated tail (e.g. "cortex.pdf"
        # because the PDF was auto-renamed by a tool that dropped the prefix).
        out["title"] = stem.replace("_", " ").strip()
        out["looks_truncated"] = (len(stem) < 30 and "_" not in stem)
    return out
