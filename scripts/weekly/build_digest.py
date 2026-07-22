#!/usr/bin/env python3
"""
scripts/weekly/build_digest.py — REFILL each researcher's "이번 주 논문 추천" back
up to PAPERS_PER_DIGEST active (unread) papers, staging new rows in
archive_weekly_digests.

P23 follow-up model (rolling 1-for-1 replace): a researcher's active set = the
digest rows that are actually OCCUPYING a slot on their board. capture_responses
marks a checked paper already_read (consumed) and archives its Notion page,
freeing a slot. This script tops the active set back up:
  - active == 0 (first fill / all consumed): pick PAPERS_PER_DIGEST honouring the
    tier {S1,A2,B2} × chunk {recent3,mid1,classic1} distribution (the solver).
  - active  > 0 (partial refill): add the (target − active) best-composite
    eligible papers.
Eligible = in the researcher's pre-built queue, NOT already in archive_responses,
NOT HELD by an archive_weekly_digests row (see below), and not out-of-scope.

SLOT OCCUPANCY (P34 S4-deadlock fix). `response_choice IS NULL` alone is NOT
occupancy. Before this fix, `_active_counts()` counted every unanswered digest
row ever written, with no week filter and no delivery check, so the 35 leftover
2026-W23 smoke-test rows (staged 2026-06-01, never seen by a researcher, never
answered) pinned active=5 for all seven researchers => deficit=0 => "full, no
refill" **forever**. The only escape was a response, and a response needs the
send path, which is gated off (state/.P23_ENABLED absent) — an unbreakable
deadlock that a dry-run reported as health. A pending row now holds a slot only
while it is plausibly ON the board:

  live      response_choice IS NULL, notion_page_id IS NOT NULL,
            sent_at within ACTIVE_WINDOW_DAYS   -> holds a slot
  in-flight response_choice IS NULL, notion_page_id IS NULL,
            sent_at within STAGED_GRACE_DAYS    -> holds a slot (staged, send
            is imminent; keeps a same-week re-run from staging a 6th paper)
  released  pending but neither of the above    -> holds NOTHING: the send
            failed/never happened, or the recommendation aged out unanswered.
            Its slot refills AND the paper returns to the candidate pool.

The same predicate governs eligibility (defect (b)): the old blanket
`NOT EXISTS (… archive_weekly_digests …)` burned a canonical_id permanently the
instant it was staged — even for a row that was never delivered — so the 35
smoke papers could never be recommended again. A digest row now blocks its paper
only while it is ANSWERED (response_choice IS NOT NULL, incl. 'expired' — a real
decision, permanent, matching archive_responses) or still HOLDING a slot. This
restores the P23 design's cooldown semantics (archive_paper_cooldown: unanswered
sends suppress for a window, then release) that the rolling model dropped.
NOTHING IS DELETED — the ledger rows stay; only their interpretation changes.

Why it slices the PRE-BUILT queue (no re-embedding): the unattended path is
ML-free (DECISIONS-v3). The stored tier/composite/chunk reflect the last operator
`build_researcher_queue.py --apply`; belief-driven re-ranking happens at that
rebuild, not in cron.

SHOWN-CONTEXT LOG (P34 S6). Every staged row also appends one JSON line to
state/archive/digest_shown_context.jsonl recording what the ranker actually
showed at that moment — queue rank_in_chunk/tier/chunk/composite/similarity/
builder, the selection mode, the slot state it filled, and the policy constants
in force. archive_weekly_digests keeps only tier_at_send, so without this the
ranking that produced a week's board is unrecoverable once the queue is rebuilt
(build_researcher_queue --apply prunes by build_token) and every unlogged week is
permanently un-analysable. Keyed by (week_iso, researcher_id, canonical_id) = the
digest UNIQUE key, so it joins straight back to the ledger row; append-only,
first-write-wins. If a future migration adds columns/JSONB to the digest table,
the same record dict is what should be written there.

Boundary: writes ONLY archive_weekly_digests (+ the local shown-context log;
reads queues/papers/synopses/responses/digests). archive_responses is never
touched here. --apply gates the DB write. No Notion, no send.

Usage:
    python3 scripts/weekly/build_digest.py                 # dry-run, all consented
    python3 scripts/weekly/build_digest.py --apply         # refill + write rows
    python3 scripts/weekly/build_digest.py --only JOP      # one researcher
    python3 scripts/weekly/build_digest.py --week 2026-W23 # pin the stamped ISO week
    python3 scripts/weekly/build_digest.py --emit-shown-context   # preview the log
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from itertools import product
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402
# same_work: pure preprint<->published twin test (real-DOI-equal OR strong
# title match when a DOI is synthetic/None). Import is side-effect-free — no
# DB connection at load — so the module still imports offline for unit tests.
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "archive"))
from _common import same_work, _token_set_ratio as _RAPIDFUZZ  # noqa: E402

KST = timezone(timedelta(hours=9))

# Distribution policy (design §3 — tunable). Both sum to PAPERS_PER_DIGEST.
TIER_TARGETS = {"S": 1, "A": 2, "B": 2}
CHUNK_TARGETS = {"recent": 3, "mid": 1, "classic": 1}
PAPERS_PER_DIGEST = sum(TIER_TARGETS.values())   # 5 (active-set target)
_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}
_CHUNKS = ("recent", "mid", "classic")

# --- slot-occupancy windows (P34 S4; see module docstring) -------------------
# A DELIVERED but unanswered recommendation holds its slot for at most 4 weeks.
# Rationale: the cadence is weekly, so 4 weeks = 4 unanswered refill cycles; the
# retired expire_pending sweep and the dry-run preview both used a 4-week
# fail-safe (CLAUDE.md P23), and the P23 cooldown view capped suppression at 8.
# A row STAGED but never delivered (notion_page_id IS NULL — send blocked, send
# never ran, or capture_responses nulled the page id reconciling an orphan)
# holds its slot only over one weekly cycle + slack; after that the send has
# demonstrably failed and must not wedge the board.
ACTIVE_WINDOW_DAYS = 28
STAGED_GRACE_DAYS = 10
_MAX_WINDOW_DAYS = 3650          # bound on the CLI overrides (0 = no limit)

# P34 S6 — shown-context provenance log (append-only JSONL, one line per staged
# row). state/archive/*.jsonl is gitignored runtime state, like every other
# regenerable artifact in that tree.
SHOWN_CONTEXT_PATH = _REPO_ROOT / "state" / "archive" / "digest_shown_context.jsonl"

_INIT_RE = re.compile(r"^[A-Z]{2,8}$")
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _git_rev() -> str | None:
    """Short HEAD sha, read straight from .git (no subprocess, offline-safe).
    Recorded in the shown-context log so a board can be tied to the ranker code
    that produced it. None if this is not a git checkout."""
    try:
        head = (_REPO_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = (_REPO_ROOT / ".git" / head.split(None, 1)[1].strip())
            if ref.exists():
                return ref.read_text(encoding="utf-8").strip()[:12]
            packed = (_REPO_ROOT / ".git" / "packed-refs")
            if packed.exists():
                want = head.split(None, 1)[1].strip()
                for line in packed.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == want:
                        return parts[0][:12]
            return None
        return head[:12] or None
    except OSError:
        return None


# --------------------------------------------------------------- candidate read

def _enabled_researchers(sch: str, only: str | None) -> list[str]:
    rows = query_json(
        f"SELECT researcher_id FROM {sch}.archive_researcher_channels "
        f"WHERE channel_type = 'notion' AND enabled = true "
        f"ORDER BY researcher_id"
    )
    rids = [r["researcher_id"] for r in rows]
    if only:
        only = only.strip().upper()
        rids = [r for r in rids if r == only]
    return rids


def _days(n: int) -> int:
    """Validate a window in days. 0 => no recency limit. Real branch (not an
    assert) so it survives `python -O`; the value is interpolated into SQL."""
    n = int(n)
    if n < 0 or n > _MAX_WINDOW_DAYS:
        raise ValueError(f"window must be 0..{_MAX_WINDOW_DAYS} days, got {n}")
    return n


def _within(alias: str, days: int) -> str:
    """SQL: `alias.sent_at` is inside a `days` window (TRUE when days == 0)."""
    days = _days(days)
    return "TRUE" if days == 0 else f"{alias}.sent_at >= now() - interval '{days} days'"


def _holds_clause(alias: str, active_days: int, grace_days: int, *,
                  delivered_forever: bool = False) -> str:
    """SQL predicate: this digest row still HOLDS its (researcher, paper) —
    i.e. the paper is answered (permanent) or is still occupying a board slot.

    The single source of truth for BOTH defects the P34 review found: the slot
    count (a) and the eligibility guard (b) are the same rule, so a row can
    never free a slot while still burning its paper, or vice versa.

    delivered_forever (--hold-delivered-forever) is the one deliberate
    exception, for ELIGIBILITY only: a row that once got a notion_page_id keeps
    its paper excluded even after its slot is released. `notion_page_id IS NOT
    NULL` proves a Notion page was created, not that a researcher ever saw it
    (the 2026-W23 boards were never shared), so the default releases those
    papers; but if the old pages are still live on a board, re-picking one shows
    the same paper twice. This flag lets the operator un-wedge with zero
    duplicate-page risk, at the cost of leaving the 35 smoke papers burned."""
    delivered_window = "TRUE" if delivered_forever else _within(alias, active_days)
    return (f"({alias}.response_choice IS NOT NULL"
            f" OR ({alias}.notion_page_id IS NOT NULL AND {delivered_window})"
            f" OR ({alias}.notion_page_id IS NULL     AND {_within(alias, grace_days)}))")


def _classify_pending(rows: list[dict], active_days: int, grace_days: int) -> dict[str, dict]:
    """Classify every PENDING (response_choice IS NULL) digest row into
    live / in_flight / released — pure function over
    {researcher_id, canonical_id, delivered, age_days} dicts, so the deadlock
    rule is unit-testable offline with no DB.

    Returns rid -> {"live": n, "in_flight": n, "released": [cid, …],
                    "active": live + in_flight}."""
    ad, gd = _days(active_days), _days(grace_days)
    out: dict[str, dict] = {}
    for r in rows:
        rid = r["researcher_id"]
        st = out.setdefault(rid, {"live": 0, "in_flight": 0, "released": [], "active": 0})
        age = float(r.get("age_days") or 0.0)
        delivered = bool(r.get("delivered"))
        if delivered and (ad == 0 or age <= ad):
            st["live"] += 1
        elif (not delivered) and (gd == 0 or age <= gd):
            st["in_flight"] += 1
        else:
            st["released"].append(r["canonical_id"])
    for st in out.values():
        st["active"] = st["live"] + st["in_flight"]
    return out


def _slot_state(sch: str, active_days: int, grace_days: int) -> dict[str, dict]:
    """Per-researcher slot state. Replaces the old _active_counts(), which
    counted `response_choice IS NULL` with no week filter and no delivery check
    and therefore let 7-week-old, never-delivered smoke rows pin active=5 for
    every researcher, permanently (P34 S4-deadlock (a))."""
    rows = query_json(
        f"SELECT researcher_id, canonical_id, "
        f"       (notion_page_id IS NOT NULL) AS delivered, "
        f"       EXTRACT(EPOCH FROM (now() - sent_at)) / 86400.0 AS age_days "
        f"  FROM {sch}.archive_weekly_digests "
        f" WHERE response_choice IS NULL")
    return _classify_pending(rows, active_days, grace_days)


def _candidates(sch: str, rid: str, active_days: int, grace_days: int, *,
                delivered_forever: bool = False) -> list[dict]:
    """Eligible queue rows for one researcher, best-composite first.

    Eligible = in the queue written by the authoritative builder ('brq' —
    build_researcher_queue.py; the parked P28 recommend.py writes 'p28' and is
    deliberately NOT read here, MF-1), not already answered (archive_responses),
    not HELD by an archive_weekly_digests row (_holds_clause: answered, or still
    occupying a slot — so an answered or on-the-board paper is never
    re-recommended, but a never-delivered / aged-out staged row no longer burns
    its paper for ever, P34 S4-deadlock (b)), and not out-of-scope. Returns
    builder + doi + title_norm too, for the shown-context log (S6) and so
    _drop_same_work() can run the same_work() twin test against the researcher's
    known works. rid is the only interpolated free text and is regex-validated
    (real branch, not assert — survives `python -O`); the windows go through
    _days()."""
    if not _INIT_RE.match(rid or ""):
        raise ValueError(f"unsafe researcher id (not /^[A-Z]{{2,8}}$/): {rid!r}")
    return query_json(f"""
        SELECT q.canonical_id, q.chunk, q.tier, q.composite, q.similarity,
               q.rank_in_chunk, q.builder,
               p.title, p.year, p.doi, p.title_norm
          FROM {sch}.archive_researcher_queues q
          JOIN {sch}.archive_papers p
            ON p.canonical_id = q.canonical_id
          LEFT JOIN {sch}.archive_paper_synopses s
            ON s.canonical_id = q.canonical_id
         WHERE q.researcher_id = '{rid}'
           AND q.builder = 'brq'
           AND s.out_of_scope_note IS NULL
           AND NOT EXISTS (
                 SELECT 1 FROM {sch}.archive_responses r
                  WHERE r.researcher_id = q.researcher_id
                    AND r.canonical_id  = q.canonical_id)
           AND NOT EXISTS (
                 SELECT 1 FROM {sch}.archive_weekly_digests w
                  WHERE w.researcher_id = q.researcher_id
                    AND w.canonical_id  = q.canonical_id
                    AND {_holds_clause('w', active_days, grace_days,
                                       delivered_forever=delivered_forever)})
         ORDER BY q.composite DESC NULLS LAST, q.similarity DESC NULLS LAST
    """)


def _known_works(sch: str, rid: str, active_days: int, grace_days: int, *,
                 delivered_forever: bool = False) -> list[dict]:
    """Every work this researcher already KNOWS — answered (archive_responses)
    or staged in a digest row that still HOLDS it (_holds_clause) — as
    {doi, title_norm} dicts for the same_work() twin test.

    The digest branch carries the SAME hold predicate as _candidates. Without
    that, releasing a never-delivered row (defect (b)) would be silently undone
    here: the released candidate shares its own old row's DOI, so same_work()
    would drop it again and the deadlock would survive one layer down.

    The cheap canonical_id NOT EXISTS guards in _candidates already drop exact
    re-recommends; this set is what the same_work() pass compares against to
    ALSO drop a preprint<->published (or cross-source) TWIN that slipped
    through because it carries a *different* canonical_id (MF-2). Reads only —
    the same archive_responses/archive_weekly_digests read plane build_digest
    already uses; no new prod path, no write. rid regex-validated (real branch,
    survives `python -O`)."""
    if not _INIT_RE.match(rid or ""):
        raise ValueError(f"unsafe researcher id (not /^[A-Z]{{2,8}}$/): {rid!r}")
    return query_json(f"""
        SELECT p.doi, p.title_norm
          FROM {sch}.archive_responses r
          JOIN {sch}.archive_papers p ON p.canonical_id = r.canonical_id
         WHERE r.researcher_id = '{rid}'
        UNION
        SELECT p.doi, p.title_norm
          FROM {sch}.archive_weekly_digests w
          JOIN {sch}.archive_papers p ON p.canonical_id = w.canonical_id
         WHERE w.researcher_id = '{rid}'
           AND {_holds_clause('w', active_days, grace_days,
                              delivered_forever=delivered_forever)}
    """)


# --------------------------------------------------------------- selection


def _drop_same_work(cands: list[dict], known: list[dict]) -> list[dict]:
    """Drop any candidate that is the SAME WORK as a paper the researcher
    already knows (answered or past-digest), closing the preprint<->published
    twin leak that the canonical_id NOT EXISTS SQL guards miss because the twin
    carries a *different* canonical_id (MF-2).

    Pure function (no DB): _common.same_work() over {doi, title_norm} dicts —
    merges on equal real DOI, or a strong rapidfuzz title match when a DOI is
    synthetic/None; it NEVER title-merges two distinct real DOIs, and a blank
    title on the DOI-missing side keeps both. Applied to the whole candidate
    list up front so BOTH refill paths (active==0 solver AND active>0
    best-composite top-up) are twin-suppressed. Survivors keep original order.
    """
    if not known:
        return list(cands)
    kept: list[dict] = []
    for c in cands:
        if any(same_work(c, k) for k in known):
            continue
        kept.append(c)
    return kept


def _compositions(total: int, parts: int):
    """Yield every non-negative integer tuple of length `parts` summing to
    `total` (compositions with zeros allowed)."""
    if parts == 1:
        yield (total,)
        return
    for first in range(total + 1):
        for rest in _compositions(total - first, parts - 1):
            yield (first,) + rest


def _select(cands: list[dict]) -> tuple[list[dict], str]:
    """Pick PAPERS_PER_DIGEST papers honouring the tier + chunk distribution.

    Returns (chosen, mode) where mode records how strictly the targets were
    met: 'strict' (both), 'chunk_relaxed' (tier kept), 'tier_relaxed', or
    'fill' (top-composite fallback). Always returns ≤ PAPERS_PER_DIGEST,
    de-duplicated by canonical_id.
    """
    tiers = list(TIER_TARGETS.keys())               # S, A, B
    # Bucket S/A/B candidates by (tier, chunk), each sorted by composite desc
    # (the candidate list already arrives composite-desc, so order is kept).
    cell: dict[tuple[str, str], list[dict]] = {(t, c): [] for t in tiers for c in _CHUNKS}
    for r in cands:
        t, c = r.get("tier"), r.get("chunk")
        if t in TIER_TARGETS and c in CHUNK_TARGETS:
            cell[(t, c)].append(r)

    # --- strict: exact tier rows AND exact chunk cols, max composite ---
    per_tier_comp = {t: list(_compositions(TIER_TARGETS[t], len(_CHUNKS))) for t in tiers}
    best_score, best_pick = None, None
    for combo in product(*(per_tier_comp[t] for t in tiers)):
        # combo[i] is the chunk-distribution tuple for tier tiers[i]
        col = [0, 0, 0]
        feasible = True
        for ti, dist in enumerate(combo):
            for ci, n in enumerate(dist):
                if n > len(cell[(tiers[ti], _CHUNKS[ci])]):
                    feasible = False
                    break
                col[ci] += n
            if not feasible:
                break
        if not feasible:
            continue
        if col != [CHUNK_TARGETS[c] for c in _CHUNKS]:
            continue
        pick, score = [], 0.0
        for ti, dist in enumerate(combo):
            for ci, n in enumerate(dist):
                for r in cell[(tiers[ti], _CHUNKS[ci])][:n]:
                    pick.append(r)
                    score += float(r.get("composite") or 0.0)
        if best_score is None or score > best_score:
            best_score, best_pick = score, pick
    if best_pick is not None:
        return best_pick, "strict"

    # --- chunk_relaxed: keep tier counts, ignore chunk distribution ---
    by_tier: dict[str, list[dict]] = {t: [] for t in tiers}
    for r in cands:
        if r.get("tier") in TIER_TARGETS:
            by_tier[r["tier"]].append(r)
    if all(len(by_tier[t]) >= TIER_TARGETS[t] for t in tiers):
        pick = []
        for t in tiers:
            pick.extend(by_tier[t][: TIER_TARGETS[t]])
        return pick, "chunk_relaxed"

    # --- tier_relaxed: take what each tier can give, fill the rest by composite ---
    pick, used = [], set()
    for t in tiers:
        take = by_tier[t][: TIER_TARGETS[t]]
        pick.extend(take)
        used.update(id(r) for r in take)
    if len(pick) < PAPERS_PER_DIGEST:
        for r in cands:                       # cands already composite-desc
            if id(r) in used:
                continue
            pick.append(r)
            used.add(id(r))
            if len(pick) >= PAPERS_PER_DIGEST:
                break
        return pick[:PAPERS_PER_DIGEST], "tier_relaxed"
    return pick[:PAPERS_PER_DIGEST], "chunk_relaxed"


def _rank(chosen: list[dict]) -> list[dict]:
    """Order the chosen papers: S→A→B→C, then composite desc. Assigns
    rank_in_digest 1..N."""
    ordered = sorted(
        chosen,
        key=lambda r: (_TIER_ORDER.get(r.get("tier"), 9), -float(r.get("composite") or 0.0)),
    )
    for i, r in enumerate(ordered, start=1):
        r["_rank"] = i
    return ordered


def _dist(chosen: list[dict]) -> str:
    tc = {}
    cc = {}
    for r in chosen:
        tc[r.get("tier")] = tc.get(r.get("tier"), 0) + 1
        cc[r.get("chunk")] = cc.get(r.get("chunk"), 0) + 1
    return (f"tier={{{', '.join(f'{k}:{tc[k]}' for k in sorted(tc))}}} "
            f"chunk={{{', '.join(f'{k}:{cc[k]}' for k in sorted(cc))}}}")


# ------------------------------------------------- shown-context log (P34 S6)

def _shown_record(week: str, rid: str, row: dict, *, mode: str, slot: dict,
                  deficit: int, pool: int, pool_raw: int, policy: dict,
                  re_recommend: bool) -> dict:
    """One provenance record: exactly what the ranker showed for this slot.

    Keyed by the digest UNIQUE key so it joins back to archive_weekly_digests.
    `queue` is the stored ranking the row was sliced from — once
    build_researcher_queue.py --apply prunes and rewrites the queue, this is the
    ONLY surviving record of the composite/tier/rank that put the paper on a
    researcher's board, i.e. the only way a later ranking change can ever be
    evaluated against what actually shipped."""
    return {
        "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week_iso": week,
        "researcher_id": rid,
        "canonical_id": row["canonical_id"],
        "rank_in_digest": row.get("_rank"),
        "tier_at_send": row.get("tier") or "C",
        "queue": {
            "builder": row.get("builder"),
            "tier": row.get("tier"),
            "chunk": row.get("chunk"),
            "composite": (None if row.get("composite") is None
                          else float(row["composite"])),
            "similarity": (None if row.get("similarity") is None
                           else float(row["similarity"])),
            "rank_in_chunk": row.get("rank_in_chunk"),
        },
        "selection": {
            "mode": mode,
            "deficit": deficit,
            "candidate_pool": pool,
            "candidate_pool_before_twin_drop": pool_raw,
            "re_recommend_after_release": re_recommend,
            "slot_state_before": {"active": slot["active"], "live": slot["live"],
                                  "in_flight": slot["in_flight"],
                                  "released": len(slot["released"])},
        },
        "paper": {"title": row.get("title"), "year": row.get("year"),
                  "doi": row.get("doi")},
        "policy": policy,
    }


def _log_shown_context(path: Path, records: list[dict]) -> tuple[int, int]:
    """Append records to the JSONL log, first-write-wins on
    (week_iso, researcher_id, canonical_id). Returns (written, skipped).

    Best-effort by design: a logging failure must never lose a staged board, so
    OSError is reported and swallowed by the caller."""
    seen: set[tuple] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue                      # tolerate a torn tail line
            seen.add((r.get("week_iso"), r.get("researcher_id"), r.get("canonical_id")))
    fresh = []
    for r in records:
        key = (r["week_iso"], r["researcher_id"], r["canonical_id"])
        if key in seen:
            continue
        seen.add(key)
        fresh.append(r)
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(fresh), len(records) - len(fresh)


# --------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write rows to DB.")
    ap.add_argument("--only", default=None, help="Build for one researcher only.")
    ap.add_argument("--week", default=None,
                    help="ISO week override (e.g. 2026-W23). Default: this week (KST).")
    ap.add_argument("--strict-fill", action="store_true",
                    help="Exit nonzero if any researcher's digest is underfilled "
                         "(< PAPERS_PER_DIGEST). Default: warn loudly but succeed "
                         "so the chain still sends the rows that were staged.")
    ap.add_argument("--active-window-days", type=int, default=ACTIVE_WINDOW_DAYS,
                    help=f"A DELIVERED unanswered row holds its slot this long "
                         f"(default {ACTIVE_WINDOW_DAYS}; 0 = for ever, the old "
                         f"pre-P34 behaviour).")
    ap.add_argument("--staged-grace-days", type=int, default=STAGED_GRACE_DAYS,
                    help=f"A staged-but-never-delivered row holds its slot this "
                         f"long (default {STAGED_GRACE_DAYS}; 0 = for ever).")
    ap.add_argument("--hold-delivered-forever", action="store_true",
                    help="Eligibility only: a paper that ever got a Notion page "
                         "stays excluded even after its slot is released. Use if "
                         "the old (unanswered) pages are still live on the board "
                         "and a re-pick would look like a duplicate.")
    ap.add_argument("--shown-context-path", default=str(SHOWN_CONTEXT_PATH),
                    help="Append-only JSONL provenance log of what was shown.")
    ap.add_argument("--emit-shown-context", action="store_true",
                    help="Print the shown-context records for this run to stdout "
                         "(dry-run: preview only, nothing is written).")
    args = ap.parse_args()

    try:
        active_days = _days(args.active_window_days)
        grace_days = _days(args.staged_grace_days)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # MF-2 guard visibility: same_work()'s fuzzy title branch is a no-op without
    # rapidfuzz, so a read/rejected preprint's DRIFTED-title published twin could
    # slip back into the digest. Real-DOI-equal twins are still caught. Warn so
    # the operator knows to `pip install rapidfuzz` for full twin suppression.
    if _RAPIDFUZZ is None:
        print("[digest] WARN: rapidfuzz not installed — same-work twin suppression "
              "(drifted-title preprint<->published) is INERT (MF-2); install with "
              "`pip install --user rapidfuzz`.", file=sys.stderr)

    load_env()
    sch = ledger_schema()
    week = args.week or iso_week(datetime.now(KST))
    if not _WEEK_RE.match(week):
        print(f"ERROR: --week must look like 2026-W23, got {week!r}", file=sys.stderr)
        return 2

    rids = _enabled_researchers(sch, args.only)
    if not rids:
        print(f"[digest] no enabled researchers in archive_researcher_channels"
              f"{' matching ' + args.only if args.only else ''}. "
              f"Seed consent rows first.")
        return 0

    _empty_slot = {"live": 0, "in_flight": 0, "released": [], "active": 0}
    slots = _slot_state(sch, active_days, grace_days)
    policy = {
        "papers_per_digest": PAPERS_PER_DIGEST,
        "tier_targets": dict(TIER_TARGETS),
        "chunk_targets": dict(CHUNK_TARGETS),
        "active_window_days": active_days,
        "staged_grace_days": grace_days,
        "hold_delivered_forever": bool(args.hold_delivered_forever),
        "builder": "brq",
        "rapidfuzz": _RAPIDFUZZ is not None,
        "code_rev": _git_rev(),
        "script": "scripts/weekly/build_digest.py",
    }
    print(f"[digest] week={week}  researchers={rids}  target={PAPERS_PER_DIGEST}/researcher  "
          f"windows: delivered≤{active_days or '∞'}d, staged≤{grace_days or '∞'}d")
    _now_str = ", ".join(f"{r}:{slots.get(r, _empty_slot)['active']}" for r in rids)
    print(f"[digest] active_now={{{_now_str}}}  (slot-holding rows only)")
    n_released = sum(len(slots.get(r, _empty_slot)["released"]) for r in rids)
    if n_released:
        print(f"[digest] {n_released} pending row(s) RELEASED (never delivered, or "
              f"delivered and unanswered past the window): they no longer hold a "
              f"slot and their papers are eligible again. Ledger rows untouched.")
    insert_rows: list[tuple] = []
    shown: list[dict] = []        # P34 S6 provenance records for this run
    underfilled: list[str] = []   # researcher → still < target after refill
    for rid in rids:
        slot = slots.get(rid, _empty_slot)
        active = slot["active"]
        deficit = PAPERS_PER_DIGEST - active
        released = set(slot["released"])
        if deficit <= 0:
            print(f"[digest] {rid}: {active} active "
                  f"(live={slot['live']}, in-flight={slot['in_flight']}) — full, no refill")
            continue
        cands = _candidates(sch, rid, active_days, grace_days,
                            delivered_forever=args.hold_delivered_forever)
        pool_raw = len(cands)
        # Same-work (twin) pass: the canonical_id NOT EXISTS SQL guards in
        # _candidates catch exact re-recommends; this drops a preprint<->
        # published twin whose canonical_id differs (MF-2). Applied to the full
        # candidate list before the active-branch split so the top-up path is
        # suppressed too. Filtered count feeds the underfill/"0 eligible" logic.
        cands = _drop_same_work(cands, _known_works(
            sch, rid, active_days, grace_days,
            delivered_forever=args.hold_delivered_forever))
        if not cands:
            print(f"[digest] {rid}: active={active}, ⚠ 0 eligible candidates to "
                  f"refill {deficit} — queue exhausted; rebuild it")
            underfilled.append(f"{rid}({active}/{PAPERS_PER_DIGEST})")
            continue
        if active == 0:
            chosen, mode = _select(cands)          # full build w/ distribution
        else:
            chosen, mode = cands[:deficit], "topup"  # best-composite top-up
        chosen = chosen[:deficit]
        if not chosen:
            print(f"[digest] {rid}: ⚠ selection produced 0 papers — skipping")
            underfilled.append(f"{rid}({active}/{PAPERS_PER_DIGEST})")
            continue
        chosen = _rank(chosen)
        got = len(chosen)
        flag = f"  [{mode}]"
        if active + got < PAPERS_PER_DIGEST:
            flag += f"  ⚠ {active+got}/{PAPERS_PER_DIGEST} (thin pool: {len(cands)} cand)"
            underfilled.append(f"{rid}({active+got}/{PAPERS_PER_DIGEST})")
        print(f"[digest] {rid}: active={active} (live={slot['live']}, "
              f"in-flight={slot['in_flight']}, released={len(released)}) "
              f"+{got} new  {_dist(chosen)}{flag}")
        for r in chosen:
            title = (r.get("title") or "")[:70]
            # ↺ = this paper had been staged before and was RELEASED by the new
            # hold rule. Worth seeing: if that old row was actually delivered,
            # its Notion page may still be live on the board (the operator's
            # banked W23 clean-up), so a re-pick can look like a duplicate.
            again = "↺" if r["canonical_id"] in released else "+"
            print(f"           {again}[{r.get('tier')}/{r.get('chunk')}] "
                  f"c={float(r.get('composite') or 0):.3f}  {r.get('year') or '----'}  {title}")
            insert_rows.append((week, rid, r["canonical_id"],
                                r.get("tier") or "C", active + r["_rank"]))
            shown.append(_shown_record(
                week, rid, r, mode=mode, slot=slot, deficit=deficit,
                pool=len(cands), pool_raw=pool_raw, policy=policy,
                re_recommend=r["canonical_id"] in released))

    print(f"\n[digest] total new rows staged: {len(insert_rows)}")
    if underfilled:
        # Loud alert so a chronically short digest never passes silently
        # (codex finding). Non-fatal by default; --strict-fill makes it fatal.
        print(f"[digest] ⚠⚠ UNDERFILLED after refill: {', '.join(underfilled)}. "
              f"Their queue is exhausted — operator should run "
              f"build_researcher_queue.py --apply to grow it.")
    if args.emit_shown_context:
        # Preview the exact records; in dry-run this is the ONLY output of the
        # S6 layer (nothing is written), so the format stays verifiable offline.
        print(f"[digest] --- shown-context records ({len(shown)}) ---")
        for rec in shown:
            print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        print(f"[digest] dry-run only. Re-run with --apply to INSERT "
              f"(and to log {len(shown)} shown-context record(s)).")
        return 3 if (underfilled and args.strict_fill) else 0
    if not insert_rows:
        print("[digest] nothing to insert.")
        return 3 if (underfilled and args.strict_fill) else 0
    sql = (f"INSERT INTO {sch}.archive_weekly_digests "
           f"(week_iso, researcher_id, canonical_id, tier_at_send, rank_in_digest, "
           f"sent_at, response_choice) "
           f"VALUES (%s,%s,%s,%s,%s, now(), NULL) "
           f"ON CONFLICT (week_iso, researcher_id, canonical_id) DO NOTHING")
    n = exec_many(sql, insert_rows)
    print(f"[digest] INSERT attempted for {n} rows (ON CONFLICT DO NOTHING).")
    # S6: log AFTER the write, so the provenance file only ever describes rows
    # the ledger was actually asked to hold. Best-effort — never fail a staged
    # board because a local log could not be appended.
    try:
        w, skipped = _log_shown_context(Path(args.shown_context_path), shown)
        print(f"[digest] shown-context: +{w} record(s) → {args.shown_context_path}"
              f"{f' ({skipped} already logged)' if skipped else ''}")
    except OSError as e:
        print(f"[digest] ⚠ shown-context log FAILED ({e}) — rows are staged, but "
              f"this week's ranking context is unlogged.", file=sys.stderr)
    return 3 if (underfilled and args.strict_fill) else 0


if __name__ == "__main__":
    raise SystemExit(main())
