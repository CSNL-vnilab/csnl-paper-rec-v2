#!/usr/bin/env python3
"""
scripts/archive/build_corpus_idf.py — domain IDF over the lab's paper
archive. Used by `build_fingerprints.py` to weight researcher-extracted
phrases by inverse document frequency (so "Rate-Distortion Theory" beats
"recent paper" at scoring time even though both appear in project text).

Operator-run:
    ! python scripts/archive/build_corpus_idf.py            # dry-run report
    ! python scripts/archive/build_corpus_idf.py --apply    # write JSON

Reads state/archive/merged_papers.jsonl (title + abstract), tokenizes
unigrams and bigrams (preserving CJK and multi-word lexicon entries via
the same `known_phrases.txt` Pass-A used downstream), counts document
frequency, emits state/archive/lexicon_idf.json shaped like:

    {"version": "v1.<date>", "n_docs": 8674,
     "idf": {"term": 4.31, ...},
     "phrase_idf": {"rate-distortion theory": 6.12, ...}}

No LLM. No network. Run once after each merge_dedupe_filter --apply.
Memoized: content-hash of merged_papers.jsonl baked into the JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE   = _REPO_ROOT / "state" / "archive"
_IN_PAPERS = _ARCHIVE / "merged_papers.jsonl"
_LEXICON   = _ARCHIVE / "known_phrases.txt"
_OUT_JSON  = _ARCHIVE / "lexicon_idf.json"

# Tokenization regex — preserve Latin alnum + Hangul + Han + Kana
# code points; split on everything else.
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_À-ɏᄀ-ᇿ぀-ヿ一-鿿가-힣]+"
)


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if line.strip():
                yield json.loads(line)


def _load_lexicon() -> list[str]:
    if not _LEXICON.exists():
        return []
    out = []
    for raw in _LEXICON.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.lower())
    return out


def _compose_doc_text(p: dict) -> str:
    parts = [p.get("title") or "", p.get("abstract") or "",
             p.get("venue") or ""]
    return "\n".join(s for s in parts if s)


def _content_hash() -> str:
    """SHA-256 of merged_papers.jsonl — for memoization."""
    if not _IN_PAPERS.exists():
        return ""
    h = hashlib.sha256()
    with _IN_PAPERS.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Write lexicon_idf.json. Default: dry-run print summary.")
    ap.add_argument("--rebuild", action="store_true",
                    help="Force rebuild even if memoization hash matches.")
    args = ap.parse_args()

    if not _IN_PAPERS.exists():
        print(f"ERROR: {_IN_PAPERS} missing — run merge_dedupe_filter first.",
              file=sys.stderr)
        return 2

    src_hash = _content_hash()
    if _OUT_JSON.exists() and not args.rebuild:
        try:
            existing = json.loads(_OUT_JSON.read_text("utf-8"))
            if existing.get("source_hash") == src_hash:
                print(f"[idf] memoized — source hash {src_hash} matches "
                      f"existing {_OUT_JSON.name}; skipping (--rebuild to force).")
                return 0
        except Exception:
            pass

    lexicon_phrases = _load_lexicon()
    print(f"[idf] lexicon phrases: {len(lexicon_phrases)}")

    n_docs = 0
    df_uni: Counter = Counter()
    df_phr: Counter = Counter()

    for p in _iter_jsonl(_IN_PAPERS):
        text = _compose_doc_text(p)
        if not text.strip():
            continue
        n_docs += 1
        low = text.lower()

        # Lexicon Pass-A — multi-word phrase document frequency.
        for ph in lexicon_phrases:
            if ph in low:
                df_phr[ph] += 1

        # Unigram document frequency (no double-count within a doc).
        toks = set()
        for tok in _TOKEN_RE.findall(low):
            if len(tok) >= 3 and not tok.isdigit():
                toks.add(tok)
        for t in toks:
            df_uni[t] += 1

    # Compute IDF = log((N+1) / (df + 1)) + 1 (smoothed).
    def _idf(df: int) -> float:
        return math.log((n_docs + 1) / (df + 1)) + 1.0

    idf_uni = {t: round(_idf(c), 3) for t, c in df_uni.items()}
    idf_phr = {t: round(_idf(c), 3) for t, c in df_phr.items()}

    payload = {
        "version":      "v1." + src_hash,
        "source_hash":  src_hash,
        "n_docs":       n_docs,
        "n_unigrams":   len(idf_uni),
        "n_phrases":    len(idf_phr),
        "idf":          idf_uni,
        "phrase_idf":   idf_phr,
    }
    print(f"[idf] n_docs={n_docs}  unigrams={len(idf_uni)}  "
          f"lexicon_phrases_seen={len(idf_phr)}")
    # Show top-20 most-document-frequent lexicon phrases — these are the
    # commonly-used scientific phrases in this corpus.
    top = sorted(df_phr.items(), key=lambda kv: -kv[1])[:20]
    for ph, c in top:
        print(f"  '{ph}' df={c}  idf={_idf(c):.2f}")

    if args.apply:
        _OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        _OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"[idf] wrote {_OUT_JSON.relative_to(_REPO_ROOT)}")
    else:
        print("[idf] dry-run only. Re-run with --apply to write JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
