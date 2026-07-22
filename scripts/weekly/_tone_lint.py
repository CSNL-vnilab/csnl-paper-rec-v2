#!/usr/bin/env python3
"""
scripts/weekly/_tone_lint.py — the mechanical BANNED_TERMS backstop, ported to
the LIVE (P23 Notion) researcher-facing path.

Why this file exists
--------------------
`rules/01_tone.md:12` declares two enforcement layers, the second being a
"mechanical lint [that] parses the BANNED_TERMS fenced block ... and aborts a
unit's send on any case-insensitive substring hit". Five implementations of that
parser exist (`scripts/deliver.py:63`, `scripts/propose_followups.py:26`,
`scripts/build_dm_drafts.py:156`, `scripts/_legacy/cron_tick.py:92`,
`scripts/_legacy/propose_feedback_acks.py:55`) — and **all five sit on the
retired Slack/DM path**. The only channel that can currently reach a researcher
is the Notion digest (`scripts/weekly/send_notion.py`), which had no lint at
all, so the documented backstop had no executor. This module is that executor.

Contract
--------
`rules/01_tone.md` is the single source of truth: the term list is *parsed*, never
hardcoded here. The fenced block is read verbatim (one term per line, blank lines
dropped) exactly as `deliver.py:63-72` reads it, so all copies stay bit-identical
in behaviour.

Design constraints
------------------
* **Pure / offline.** No DB, no network, no LLM — safe to call from the
  unattended weekly chain (DECISIONS-v3: no LLM in the cron path).
* **Fail-closed.** If `rules/01_tone.md` is missing, unreadable, or carries no
  `BANNED_TERMS` block, `load_banned_terms()` raises `ToneLintUnavailable`.
  Callers on a send path must treat that as ABORT, never as "lint skipped".
* **Data contract only.** This checks the curated hard-unsafe substring set and
  nothing else. It deliberately does NOT enforce the prose-style rules (emoji,
  `!`, greeting form) — the Notion digest intentionally uses emoji section
  markers (`render.recommendation_ko`), and rules/01 scopes those to the drafting
  agent, layer 1. Widening this set would abort every legitimate row.

Self-test:  python3 scripts/weekly/_tone_lint.py
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RULES_FILE = _REPO_ROOT / "rules" / "01_tone.md"

BANNED_FENCE = "BANNED_TERMS"
_FENCE_RE = re.compile(
    r"```" + re.escape(BANNED_FENCE) + r"\s*\n(.*?)\n```", re.DOTALL)
_WS_RE = re.compile(r"\s+")

_EXCERPT_PAD = 32


class ToneLintUnavailable(RuntimeError):
    """rules/01_tone.md is missing/unreadable, or has no BANNED_TERMS block.

    A send path MUST convert this into an abort. The whole point of the backstop
    is that "no rules file" never silently degrades into "no enforcement".
    """


class Violation(NamedTuple):
    """One banned-substring hit. `field` is the caller's label for the text."""
    term: str
    index: int
    excerpt: str
    field: str = ""

    def describe(self) -> str:
        where = f"{self.field}: " if self.field else ""
        return f"{where}banned term {self.term!r} @{self.index} … {self.excerpt} …"


# --------------------------------------------------------------------- parsing

def load_banned_terms(path: Path | str | None = None) -> list[str]:
    """Parse the fenced BANNED_TERMS block out of rules/01_tone.md.

    Returns the terms verbatim (original case; matching is case-insensitive).
    Raises ToneLintUnavailable rather than returning an empty list, so a caller
    can never mistake "unreadable" for "clean".
    """
    f = Path(path) if path is not None else RULES_FILE
    try:
        text = f.read_text(encoding="utf-8")
    except OSError as e:
        raise ToneLintUnavailable(f"cannot read {f}: {type(e).__name__}: {e}") from e
    m = _FENCE_RE.search(text)
    if not m:
        raise ToneLintUnavailable(
            f"no fenced ```{BANNED_FENCE}``` block found in {f}")
    terms = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
    if not terms:
        raise ToneLintUnavailable(f"```{BANNED_FENCE}``` block in {f} is empty")
    return terms


# -------------------------------------------------------------------- checking

def _scan(haystack: str, terms: Sequence[str], field: str,
          seen: set[str]) -> list[Violation]:
    low = haystack.lower()
    out: list[Violation] = []
    for t in terms:
        tl = t.lower()
        if not tl or tl in seen:
            continue
        i = low.find(tl)
        if i < 0:
            continue
        seen.add(tl)
        lo = max(0, i - _EXCERPT_PAD)
        hi = min(len(haystack), i + len(t) + _EXCERPT_PAD)
        out.append(Violation(term=t, index=i,
                             excerpt=_WS_RE.sub(" ", haystack[lo:hi]).strip(),
                             field=field))
    return out


def check(text: str, terms: Sequence[str] | None = None,
          field: str = "") -> list[Violation]:
    """Pure check: return every banned term present in `text` (case-insensitive
    substring), one Violation per distinct term.

    `terms=None` loads rules/01_tone.md (and therefore may raise
    ToneLintUnavailable — that is the fail-closed path). Callers that lint many
    strings should load once and pass the list in.

    Beyond `deliver.py`'s plain substring pass, a second pass runs over a
    whitespace-collapsed copy so a multi-word term split across a line break
    (`"— claude"` wrapped mid-signature, `"navigate the\\ncomplexities"`) still
    trips. That strictly adds sensitivity; it cannot manufacture a hit that is
    not really in the text.
    """
    if terms is None:
        terms = load_banned_terms()
    src = text or ""
    if not src:
        return []
    seen: set[str] = set()
    hits = _scan(src, terms, field, seen)
    flat = _WS_RE.sub(" ", src)
    if flat != src:
        hits.extend(_scan(flat, terms, field, seen))
    return hits


def check_fields(fields: Mapping[str, str],
                 terms: Sequence[str] | None = None) -> list[Violation]:
    """check() over a {label: text} mapping, tagging each Violation.field."""
    if terms is None:
        terms = load_banned_terms()
    out: list[Violation] = []
    for label, text in fields.items():
        out.extend(check(text, terms, field=label))
    return out


def cap_check(text: str) -> list[str]:
    """ADVISORY (not part of the BANNED_TERMS data contract).

    rules/01:42 caps `paradigm` / `framework` at ≤1 occurrence per message.
    Ported from `deliver.py:80` so the rule has one implementation, but it is
    deliberately NOT wired into the send gate: the Notion rationale renders a
    synopsis `frameworks` list, where a second occurrence is structural rather
    than tonal. Reporting only.
    """
    hits = []
    for w in ("paradigm", "framework"):
        if len(re.findall(w, text or "", re.I)) > 1:
            hits.append(f"{w}>1")
    return hits


def format_violations(vs: Iterable[Violation], indent: str = "    ") -> str:
    return "\n".join(f"{indent}- {v.describe()}" for v in vs)


# ------------------------------------------------------------------- self-test

if __name__ == "__main__":
    _terms = load_banned_terms()
    assert len(_terms) >= 20, _terms
    assert "anthropic" in _terms and "훌륭" in _terms, _terms[:5]

    # clean researcher-facing prose passes
    _clean = ("❓ 핵심 질문: 방향 추정에서 사전 분포가 편향을 만드는가?\n"
              "🔑 주요 발견: 자극별 손실 가정이 추정 편향을 예측함\n"
              "🔗 키워드: serial dependence, efficient coding")
    assert check(_clean, _terms) == [], check(_clean, _terms)

    # a known banned term trips it
    _dirty = _clean + "\n— Claude"
    _hit = check(_dirty, _terms, field="recommendation")
    assert [v.term for v in _hit] == ["— claude"], _hit
    assert _hit[0].field == "recommendation" and _hit[0].index > 0
    assert "claude" in _hit[0].excerpt.lower()

    # case-insensitive + Korean + multiword
    assert [v.term for v in check("This is ROBUST work", _terms)] == ["robust"]
    assert [v.term for v in check("정말 훌륭합니다", _terms)] == ["훌륭"]
    assert check("navigate the\ncomplexities", _terms), "line-broken phrase"

    # legitimate metadata must not trip (rules/01:16)
    assert check("Shannon, C. E. (1948). A mathematical theory of communication.",
                 _terms) == [], "author named Claude Shannon"

    # field mapping + empties
    _fv = check_fields({"a": "openai", "b": "clean text"}, _terms)
    assert [(v.field, v.term) for v in _fv] == [("a", "openai")], _fv
    assert check("", _terms) == [] and check(None, _terms) == []

    # fail-closed
    try:
        load_banned_terms(_REPO_ROOT / "rules" / "__nope__.md")
    except ToneLintUnavailable:
        pass
    else:
        raise AssertionError("missing rules file must raise ToneLintUnavailable")

    assert cap_check("framework framework") == ["framework>1"]
    print(f"_tone_lint.py self-test OK — {len(_terms)} terms from "
          f"{RULES_FILE.relative_to(_REPO_ROOT)}")
