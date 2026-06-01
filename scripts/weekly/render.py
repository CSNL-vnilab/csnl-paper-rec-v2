#!/usr/bin/env python3
"""
scripts/weekly/render.py — deterministic, LLM-free rendering of a digest row
into the two researcher-facing strings the Notion digest needs:

  * APA-7 citation  (Notion "Title" property)  — apa_citation()
  * Korean rationale (Notion "Recommendation")  — recommendation_ko()

The rationale is assembled from the P21/P22c per-paper synopsis (core_question,
key_findings, connecting_signals, frameworks) — which WAS produced by an Opus
fan-out offline. Nothing here calls an LLM, so it is safe in the unattended
weekly cron path (DECISIONS-v3: no LLM in cron). When a synopsis is absent it
falls back to an abstract snippet.

Pure functions (string in, string out) so they unit-test without a DB or
network — see the __main__ self-test.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_ROLE_KO = {
    "primary_lens":    "주축",
    "extended":        "확장",
    "compared_against": "비교",
    "alternative_lens": "대안",
    "context":         "맥락",
}


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [v]
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _initials(given: str) -> str:
    """'Sang-Hun' -> 'S.-H.'  ; 'Samuel J.' -> 'S. J.'"""
    out = []
    for tok in given.replace("‐", "-").split():
        if not tok:
            continue
        if "-" in tok:
            out.append("-".join(p[0].upper() + "." for p in tok.split("-") if p))
        else:
            out.append(tok[0].upper() + ".")
    return " ".join(out)


def _apa_one_author(name: str) -> str:
    """Best-effort 'Family, G. H.' from either 'Family, Given' or 'Given Family'."""
    name = (name or "").replace("‐", "-").strip()
    if not name:
        return ""
    if "," in name:
        family, _, given = name.partition(",")
        family, given = family.strip(), given.strip()
    else:
        toks = name.split()
        if len(toks) == 1:
            return toks[0]
        family, given = toks[-1], " ".join(toks[:-1])
    ini = _initials(given)
    return f"{family}, {ini}".strip().rstrip(",") if ini else family


def apa_authors(authors_json: Any) -> str:
    authors = [a for a in (_as_list(authors_json)) if str(a).strip()]
    names = [_apa_one_author(str(a)) for a in authors]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    # APA 7: list up to 20, then "… &" the last; join with commas + ampersand.
    if len(names) > 20:
        names = names[:19] + ["…", names[-1]]
        return ", ".join(names[:-1]) + " " + names[-1]
    return ", ".join(names[:-1]) + ", & " + names[-1]


def apa_citation(paper: dict) -> str:
    """One-line APA-7-shaped citation. Title/venue kept verbatim (P19f: no
    Korean wrapping, copy-pasteable). Volume/issue/pages are usually absent in
    the archive, so the form is Authors (Year). Title. Venue. DOI-URL."""
    auth = apa_authors(paper.get("authors_json"))
    year = paper.get("year")
    yr = f"({year})" if year else "(n.d.)"
    title = (paper.get("title") or "").strip().rstrip(".")
    venue = (paper.get("venue") or "").strip().rstrip(".")
    doi = (paper.get("doi") or "").strip()
    parts = []
    if auth:
        parts.append(f"{auth} {yr}.")
    else:
        parts.append(f"{yr}.")
    if title:
        parts.append(f"{title}.")
    if venue:
        parts.append(f"{venue}.")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    return " ".join(parts).strip()


def doi_url(paper: dict) -> Optional[str]:
    doi = (paper.get("doi") or "").strip()
    return f"https://doi.org/{doi}" if doi else None


def paper_title(paper: dict) -> str:
    """The paper title verbatim (the Notion 'Title' column)."""
    return (paper.get("title") or "").strip()


def authors_str(paper: dict) -> str:
    """APA-style author list only (the Notion '저자' column)."""
    return apa_authors(paper.get("authors_json"))


def _frameworks_ko(frameworks: Any) -> str:
    fws = _as_list(frameworks)
    bits = []
    for fw in fws:
        if not isinstance(fw, dict):
            continue
        name = (fw.get("name") or "").strip()
        if not name:
            continue
        role = _ROLE_KO.get(fw.get("role") or "", "")
        bits.append(f"{name}({role})" if role else name)
    return ", ".join(bits[:3])


def recommendation_ko(row: dict) -> str:
    """Assemble the Korean rationale from synopsis fields, deterministically.

    `row` carries the synopsis columns (core_question, key_findings,
    connecting_signals, frameworks) and, as fallback, `abstract`.
    """
    lines: list[str] = []
    cq = (row.get("core_question") or "").strip()
    findings = [str(x).strip() for x in _as_list(row.get("key_findings")) if str(x).strip()]
    signals = [str(x).strip() for x in _as_list(row.get("connecting_signals")) if str(x).strip()]
    fw = _frameworks_ko(row.get("frameworks"))

    if cq:
        lines.append(f"❓ 핵심 질문: {cq}")
    if findings:
        lines.append("🔑 주요 발견: " + "; ".join(findings[:3]))
    if fw:
        lines.append(f"🧭 프레임워크: {fw}")
    if signals:
        lines.append("🔗 키워드: " + ", ".join(signals[:5]))

    if not lines:
        # No synopsis — fall back to a trimmed abstract so the row is never blank.
        abstract = (row.get("abstract") or "").strip()
        if abstract:
            snippet = re.sub(r"\s+", " ", abstract)[:500]
            lines.append("📄 초록 발췌: " + snippet)
        else:
            lines.append("(시놉시스/초록 정보가 없어 자동 사유를 생성하지 못했습니다. "
                         "제목과 DOI 링크를 참고해 주세요.)")
    return "\n".join(lines)


if __name__ == "__main__":  # tiny self-test
    paper = {
        "authors_json": ["Heeseung Lee", "Jaeseob Lim", "Sang‐Hun Lee"],
        "year": 2025,
        "title": "Belief updating in decision-variable space",
        "venue": "iScience",
        "doi": "10.1016/j.isci.2025.112844",
    }
    print(apa_citation(paper))
    assert apa_authors(["Heeseung Lee", "Jaeseob Lim", "Sang‐Hun Lee"]) \
        == "Lee, H., Lim, J., & Lee, S.-H.", apa_authors(paper["authors_json"])
    assert apa_authors(["Gershman, Samuel J."]) == "Gershman, S. J."
    row = {"core_question": "Q?", "key_findings": ["f1", "f2", "f3", "f4"],
           "connecting_signals": ["granularity effect", "serial dependence"],
           "frameworks": [{"name": "Bayesian", "role": "primary_lens"}]}
    r = recommendation_ko(row)
    assert "핵심 질문" in r and "f3" in r and "f4" not in r, r
    assert "granularity effect" in r and "Bayesian(주축)" in r, r
    assert recommendation_ko({"abstract": "x" * 10}).startswith("📄 초록"), "fallback"
    assert recommendation_ko({}).startswith("("), "empty fallback"
    print("render.py self-test OK")
