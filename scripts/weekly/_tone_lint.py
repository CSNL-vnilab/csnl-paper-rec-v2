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
`rules/01_tone.md` is the single source of truth for *which* terms exist: the
list is *parsed*, never hardcoded here. The fenced block is read verbatim (one
term per line, blank lines dropped) exactly as `deliver.py:63-72` reads it, so
all copies stay bit-identical in behaviour.

Severity is decided HERE, not in the rules file (P34/L3-1)
-----------------------------------------------------------
The first port of this backstop treated all 43 terms as equally fatal. Measured
against live data that blocked **69 of 1400 queue rows and 7 of the 35 rows on
the active board, for 6 of 7 researchers** — every one of them on `robust`,
`holistic` or `leverage` appearing in *the paper's own* title/synopsis
("Robust averaging protects decisions from noise…", "Holistic Bayesian model
of perceptual adaptation…", three of them the researcher's S-tier pick). A
blocked row never gets a `notion_page_id`, so `build_digest._active_counts`
keeps counting it as an occupied slot and the board silently shrinks. Blocking
those is not a tone save; it censors the science and wedges the slot.

rules/01:12-17 already says the set is curated so it "never false-positives on
a legitimate paper title or author". So the terms split by *what they identify*:

* `severity == FATAL` — **identity / signature markers**: the authoring system
  naming itself (`claude`, `anthropic`, `chatgpt`, `openai`, `gpt-4/5`,
  `as an ai`, `언어모델로서`, the `— claude` signature forms) plus coined
  internal-ops identifiers that cannot occur in natural prose (`safe_memory`,
  `q_hash`, `memev`, …). These are never a paper's own vocabulary, so blocking
  costs nothing and leaking one is a hard boundary breach (CLAUDE.md: 연구자
  노출 텍스트에 내부 용어/서명 금지). Verified: **0 occurrences across the whole
  live corpus** (titles + abstracts + synopses).
* `severity == ADVISORY` — **style / hype words** (`robust`, `comprehensive`,
  `holistic`, `synergy`, `leverage`, `delve`, `meticulous`, `tapestry`,
  `훌륭`, `매우 적합`, …) and ordinary English words that are also real science
  vocabulary (`subagent`, `orchestrator` — both appear in hierarchical-RL and
  cell-biology writing). These are layer-1 (drafting agent) concerns; the
  backstop reports them and does not stop a send.

A term the classifier does not recognise defaults to ADVISORY — adding a term
to rules/01 can therefore never silently wedge the board, it can only add a
warning. Making a *new* term fatal is a deliberate edit to `_IDENTITY_PATTERNS`.

The second half of the fix lives in the caller: severity is only half the story
if the check is pointed at quoted source text. `send_notion._visible_text()`
lints the agent-authored rationale, and treats the paper's own title / authors /
venue / abstract excerpt as quoted (advisory only).

Design constraints
------------------
* **Pure / offline.** No DB, no network, no LLM — safe to call from the
  unattended weekly chain (DECISIONS-v3: no LLM in the cron path).
* **Fail-closed.** If `rules/01_tone.md` is missing, unreadable, or carries no
  `BANNED_TERMS` block, `load_banned_terms()` raises `ToneLintUnavailable`.
  Callers on a send path must treat that as ABORT, never as "lint skipped".
  Fail-closed still holds after the split: an unreadable rules file yields no
  terms at all, fatal or advisory, and the send aborts.
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

# ------------------------------------------------------------------ severity
FATAL = "fatal"
ADVISORY = "advisory"

# Applied to the TERMS PARSED OUT OF rules/01_tone.md — never to researcher-
# facing text. It can therefore only re-classify an already-curated term; it can
# never introduce a new match. That is why it is allowed to be generous.
_IDENTITY_PATTERNS = (
    r"claude",
    r"anthropic",
    r"chatgpt",
    r"openai",
    r"gpt[\s\-_.]?\d*",                 # gpt-4, gpt-5, gpt 4o, bare gpt
    r"as\s+an\s+ai",
    r"\bai\s*(assistant|어시스턴트)",
    r"언어\s*모델",                       # 언어모델로서 / 대규모 언어 모델로서
    r"인공지능",
    r"^[\-—–]\s*\S+$",                  # a bare signature line: "— <name>"
)
_IDENTITY_RE = re.compile("|".join(_IDENTITY_PATTERNS), re.I)

# Coined internal-ops identifiers. snake_case cannot occur in natural prose;
# `memev` and friends are invented tokens. Measured 0 hits across the live
# corpus, so treating them as fatal costs no recommendation slot.
_INTERNAL_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+$")
_COINED_INTERNAL = frozenset({"memev"})


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
    severity: str = ADVISORY

    def describe(self) -> str:
        where = f"{self.field}: " if self.field else ""
        return (f"{where}[{self.severity}] banned term {self.term!r} "
                f"@{self.index} … {self.excerpt} …")


class TermSplit(NamedTuple):
    """The BANNED_TERMS list partitioned by severity (see module docstring)."""
    fatal: tuple[str, ...]
    advisory: tuple[str, ...]

    @property
    def all(self) -> list[str]:
        return list(self.fatal) + list(self.advisory)

    def __len__(self) -> int:            # so len(banned) keeps reading naturally
        return len(self.fatal) + len(self.advisory)


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


# ------------------------------------------------------------- classification

def term_severity(term: str) -> str:
    """FATAL for identity/signature/internal-ops markers, ADVISORY otherwise.

    Unrecognised terms default to ADVISORY on purpose: adding a word to
    rules/01_tone.md must never be able to silently wedge a researcher's board
    (P34/L3-1). Promoting a term to fatal is an explicit edit here.
    """
    t = (term or "").strip()
    if not t:
        return ADVISORY
    if _IDENTITY_RE.search(t):
        return FATAL
    if t.lower() in _COINED_INTERNAL or _INTERNAL_TOKEN_RE.match(t):
        return FATAL
    return ADVISORY


def split_terms(terms: Sequence[str] | None = None) -> TermSplit:
    """Partition BANNED_TERMS into (fatal, advisory), preserving file order.

    `terms=None` loads rules/01_tone.md, so this is also a fail-closed entry
    point (raises ToneLintUnavailable).
    """
    if terms is None:
        terms = load_banned_terms()
    fatal = tuple(t for t in terms if term_severity(t) == FATAL)
    adv = tuple(t for t in terms if term_severity(t) != FATAL)
    return TermSplit(fatal=fatal, advisory=adv)


def as_split(terms: TermSplit | Sequence[str] | None) -> TermSplit:
    """Accept either a raw term list or an already-partitioned TermSplit."""
    if isinstance(terms, TermSplit):
        return terms
    return split_terms(terms)


# -------------------------------------------------------------------- checking

def _scan(haystack: str, terms: Sequence[str], field: str,
          seen: set[str], severity: str = ADVISORY) -> list[Violation]:
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
                             field=field, severity=severity))
    return out


def check(text: str, terms: Sequence[str] | None = None,
          field: str = "", severity: str = ADVISORY) -> list[Violation]:
    """Pure check: return every banned term present in `text` (case-insensitive
    substring), one Violation per distinct term.

    `terms=None` loads rules/01_tone.md (and therefore may raise
    ToneLintUnavailable — that is the fail-closed path). Callers that lint many
    strings should load once and pass the list in. `severity` only labels the
    returned Violations — pass `split_terms().fatal` with `severity=FATAL` to
    run the blocking half, `.advisory` for the reporting half.

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
    hits = _scan(src, terms, field, seen, severity)
    flat = _WS_RE.sub(" ", src)
    if flat != src:
        hits.extend(_scan(flat, terms, field, seen, severity))
    return hits


def check_fields(fields: Mapping[str, str],
                 terms: Sequence[str] | None = None,
                 severity: str = ADVISORY) -> list[Violation]:
    """check() over a {label: text} mapping, tagging each Violation.field."""
    if terms is None:
        terms = load_banned_terms()
    out: list[Violation] = []
    for label, text in fields.items():
        out.extend(check(text, terms, field=label, severity=severity))
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

    # ---- severity split (P34/L3-1) ----------------------------------------
    _sp = split_terms(_terms)
    assert len(_sp) == len(_terms), (len(_sp), len(_terms))
    assert set(_sp.fatal).isdisjoint(_sp.advisory), "a term cannot be both"
    assert sorted(_sp.all) == sorted(_terms), "split must be a partition"

    # identity / signature / coined-internal -> FATAL
    for _t in ("— claude", "- claude", "—claude", "(claude)", "claude opus",
               "claude sonnet", "claude haiku", "claude code", "anthropic",
               "chatgpt", "openai", "gpt-4", "gpt-5", "as an ai",
               "ai assistant", "ai 어시스턴트", "언어모델로서",
               "대규모 언어 모델로서", "safe_memory", "member_uncertainty",
               "nas_inventory", "fire_lock", "q_hash", "memev",
               "harness_runner", "exploration_plan"):
        assert term_severity(_t) == FATAL, f"{_t!r} must be fatal"
        assert _t in _sp.fatal, f"{_t!r} missing from rules/01 fatal set"

    # style / hype / real science vocabulary -> ADVISORY (never blocks a send)
    for _t in ("robust", "comprehensive", "holistic", "synergy", "leverage",
               "delve", "tapestry", "meticulous", "navigate the complexities",
               "훌륭", "최고의", "매우 적합", "강력히 추천", "놀라운",
               "감사합니다", "subagent", "orchestrator"):
        assert term_severity(_t) == ADVISORY, f"{_t!r} must NOT block a send"
        assert _t in _sp.advisory, f"{_t!r} missing from rules/01 advisory set"

    # the L3-1 regression itself: a real paper title is advisory-only
    _paper = ("Robust averaging protects decisions from noise; a holistic "
              "model that leverages efficient coding")
    assert [v.term for v in check(_paper, _sp.fatal, severity=FATAL)] == [], \
        "paper vocabulary must never hit the fatal set"
    assert {v.term for v in check(_paper, _sp.advisory)} == \
        {"robust", "holistic", "leverage"}, "…but must still be reported"

    # …while a real identity leak still hard-blocks
    _leak = check(_paper + "\n— Claude", _sp.fatal, field="추천 근거",
                  severity=FATAL)
    assert [v.term for v in _leak] == ["— claude"], _leak
    assert _leak[0].severity == FATAL and "fatal" in _leak[0].describe()

    # unknown terms default to advisory — a rules edit can never wedge a board
    assert term_severity("완전히새로운금지어") == ADVISORY
    assert term_severity("") == ADVISORY

    # fail-closed
    try:
        load_banned_terms(_REPO_ROOT / "rules" / "__nope__.md")
    except ToneLintUnavailable:
        pass
    else:
        raise AssertionError("missing rules file must raise ToneLintUnavailable")
    try:
        split_terms(load_banned_terms(_REPO_ROOT / "rules" / "__nope__.md"))
    except ToneLintUnavailable:
        pass
    else:
        raise AssertionError("split_terms must inherit the fail-closed path")

    assert cap_check("framework framework") == ["framework>1"]
    print(f"_tone_lint.py self-test OK — {len(_terms)} terms from "
          f"{RULES_FILE.relative_to(_REPO_ROOT)} "
          f"({len(_sp.fatal)} fatal / {len(_sp.advisory)} advisory)")
