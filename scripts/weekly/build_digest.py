#!/usr/bin/env python3
"""
scripts/weekly/build_digest.py — assemble each researcher's weekly digest from
their pre-built recommendation queue and stage it in archive_weekly_digests.

Design: docs/HARNESS-WEEKLY-DELIVERY-DESIGN.md §6. Per consented researcher:
  read queue → drop already-answered / in-cooldown / out-of-scope / already-
  staged-this-week → pick 5 papers under the tier + chunk distribution →
  INSERT archive_weekly_digests rows (response_choice NULL, notion_page_id
  NULL — send_notion.py fills the page id afterwards).

Why this slices the PRE-BUILT queue (no re-embedding here): the unattended
weekly path is deliberately ML-free (same principle as run_weekly_cron.sh /
DECISIONS-v3 — no heavy model, no LLM in cron). The queue's stored tier /
composite / chunk already reflect the researcher's beliefs as of the last
operator `build_researcher_queue.py --apply`; the design's "re-rank with new
dim_preferences" (§9) is realised by that periodic operator rebuild (which the
belief-update cadence should trigger), not by re-scoring inside cron.

Boundary: this writes ONLY archive_weekly_digests (+ reads queues / papers /
synopses / responses / cooldown). archive_responses is never touched here.
Operator-run; --apply gates the DB write (dry-run otherwise).

Usage:
    python3 scripts/weekly/build_digest.py                 # dry-run, all consented
    python3 scripts/weekly/build_digest.py --apply         # write rows
    python3 scripts/weekly/build_digest.py --only JOP      # one researcher
    python3 scripts/weekly/build_digest.py --week 2026-W23 # pin the ISO week
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone, timedelta
from itertools import product
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env, query_json, exec_many, ledger_schema  # noqa: E402

KST = timezone(timedelta(hours=9))

# Distribution policy (design §3 — tunable). Both sum to PAPERS_PER_DIGEST.
TIER_TARGETS = {"S": 1, "A": 2, "B": 2}
CHUNK_TARGETS = {"recent": 3, "mid": 1, "classic": 1}
PAPERS_PER_DIGEST = sum(TIER_TARGETS.values())   # 5
COOLDOWN_WEEKS = 8
_TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3}
_CHUNKS = ("recent", "mid", "classic")

_INIT_RE = re.compile(r"^[A-Z]{2,8}$")
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


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


def _candidates(sch: str, rid: str, week: str) -> list[dict]:
    """Eligible queue rows for one researcher, best-composite first.

    All interpolated values are internal + validated (init code, ISO week,
    int weeks) — never researcher free-text — matching the inline-value read
    pattern used elsewhere in scripts/ (query_json takes no params).

    Validation uses real branches (NOT assert) so it survives `python -O`
    where assert statements are stripped (codex finding)."""
    if not _INIT_RE.match(rid or ""):
        raise ValueError(f"unsafe researcher id (not /^[A-Z]{{2,8}}$/): {rid!r}")
    if not _WEEK_RE.match(week or ""):
        raise ValueError(f"unsafe week (not /^\\d{{4}}-W\\d{{2}}$/): {week!r}")
    weeks = int(COOLDOWN_WEEKS)
    return query_json(f"""
        SELECT q.canonical_id, q.chunk, q.tier, q.composite, q.similarity,
               q.rank_in_chunk,
               p.title, p.year, p.doi
          FROM {sch}.archive_researcher_queues q
          JOIN {sch}.archive_papers p
            ON p.canonical_id = q.canonical_id
          LEFT JOIN {sch}.archive_paper_synopses s
            ON s.canonical_id = q.canonical_id
          LEFT JOIN {sch}.archive_paper_cooldown cd
            ON cd.researcher_id = q.researcher_id
           AND cd.canonical_id  = q.canonical_id
         WHERE q.researcher_id = '{rid}'
           AND s.out_of_scope_note IS NULL
           AND (cd.last_sent_at IS NULL
                OR cd.last_sent_at < now() - interval '{weeks} weeks')
           AND NOT EXISTS (
                 SELECT 1 FROM {sch}.archive_responses r
                  WHERE r.researcher_id = q.researcher_id
                    AND r.canonical_id  = q.canonical_id)
           AND NOT EXISTS (
                 SELECT 1 FROM {sch}.archive_weekly_digests w
                  WHERE w.researcher_id = q.researcher_id
                    AND w.canonical_id  = q.canonical_id
                    AND w.week_iso       = '{week}')
         ORDER BY q.composite DESC NULLS LAST, q.similarity DESC NULLS LAST
    """)


# --------------------------------------------------------------- selection

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
    args = ap.parse_args()

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

    print(f"[digest] week={week}  researchers={rids}  "
          f"policy tier={TIER_TARGETS} chunk={CHUNK_TARGETS} cooldown={COOLDOWN_WEEKS}w")
    insert_rows: list[tuple] = []
    underfilled: list[str] = []   # researcher → got < PAPERS_PER_DIGEST
    for rid in rids:
        cands = _candidates(sch, rid, week)
        if not cands:
            print(f"[digest] {rid}: ⚠ 0 eligible candidates "
                  f"(queue empty, all answered, or all in cooldown) — skipping")
            underfilled.append(f"{rid}(0)")
            continue
        chosen, mode = _select(cands)
        if not chosen:
            print(f"[digest] {rid}: ⚠ selection produced 0 papers — skipping")
            underfilled.append(f"{rid}(0)")
            continue
        chosen = _rank(chosen)
        flag = "" if mode == "strict" else f"  ⚠ {mode}"
        if len(chosen) < PAPERS_PER_DIGEST:
            flag += f"  ⚠ only {len(chosen)}/{PAPERS_PER_DIGEST} (thin pool: {len(cands)} cand)"
            underfilled.append(f"{rid}({len(chosen)})")
        print(f"[digest] {rid}: {len(chosen)} papers  {_dist(chosen)}{flag}")
        for r in chosen:
            title = (r.get("title") or "")[:70]
            print(f"           #{r['_rank']} [{r.get('tier')}/{r.get('chunk')}] "
                  f"c={float(r.get('composite') or 0):.3f}  {r.get('year') or '----'}  {title}")
            insert_rows.append((week, rid, r["canonical_id"],
                                r.get("tier") or "C", r["_rank"]))

    print(f"\n[digest] total staged rows: {len(insert_rows)} "
          f"(expected {len(rids)}×{PAPERS_PER_DIGEST}={len(rids)*PAPERS_PER_DIGEST})")
    if underfilled:
        # Loud alert so a chronically short digest never passes silently
        # (codex finding). Non-fatal by default; --strict-fill makes it fatal.
        print(f"[digest] ⚠⚠ UNDERFILLED: {len(underfilled)} researcher(s) got "
              f"< {PAPERS_PER_DIGEST} papers: {', '.join(underfilled)}. "
              f"Investigate the queue (build_researcher_queue.py) / cooldown / "
              f"answered-coverage for them.")
    if not args.apply:
        print("[digest] dry-run only. Re-run with --apply to INSERT.")
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
    return 3 if (underfilled and args.strict_fill) else 0


if __name__ == "__main__":
    raise SystemExit(main())
