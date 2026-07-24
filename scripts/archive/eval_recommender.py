#!/usr/bin/env python3
"""
scripts/archive/eval_recommender.py — held-out, READ-ONLY quality eval for the
per-researcher recommendation queue (archive_researcher_queues).

WHAT THIS IS
------------
A benign measurement harness. It runs a *temporal* train/hold-out split over
`archive_responses` per researcher, then scores the stored queue against the
held-out positives and reports recall@k (k=5,20,50), MRR, precision@5, plus a
temporal held-out precision and an auxiliary positive-vs-negative rank AUC.

It supports a BEFORE/AFTER diff keyed on researcher (`--out` snapshot +
`--baseline`) so an orchestrator can gate a queue rebuild on:

    "recall@50 for {BHL,JYK,SMJ} does not regress (and ideally improves)."

It performs NO writes to Postgres, NO sends, and NO queue rebuild. The only
thing it can write is a local snapshot JSON (when `--out` is passed). All DB
access is `SELECT` via pipeline/_db.query_json.

HONESTY / CIRCULARITY (read this before trusting the numbers)
------------------------------------------------------------
This is a **less-circular** metric, NOT a fully non-circular one.

  * validate_drift's `precision_30d` is *descriptive*: it just tallies the
    save/not-relevant ratio over recently-shown papers. It has no train/test
    separation and no notion of ranking — it cannot tell you whether a rebuild
    would surface papers the researcher will value.

  * This harness holds out the *newest* slice of each researcher's labels and
    asks whether the queue ranks those held-out positives highly. That is a
    genuine temporal hold-out (the metric is scored on labels not used to pick
    the split), so it is strictly more informative than `precision_30d`.

  * BUT it is still circular in one important way: the held-out labels only
    exist for papers the interview policy chose to show. A paper the researcher
    would have loved but was never surfaced can never be a held-out positive,
    so recall here is recall *within the policy's own candidate funnel*. A
    freshly-rebuilt queue is also scored as-stored, so if the rebuild was NOT
    a hold-out rebuild (i.e. it excluded the held-out responses at build time)
    both BEFORE and AFTER share that leakage equally — the diff stays fair, the
    absolute number stays optimistic.

  * The fully non-circular version needs *external* labels that do not come
    from the recommender's own funnel — e.g. the CWLL reading-group log or the
    reference lists of the lab's own manuscripts — matched against the queue.
    That is the documented follow-up (it needs an ingest of those sources into
    a labelled ground-truth table); it is intentionally NOT done here.

    For the AFTER leg of a gate to be a clean hold-out, the rebuild should be
    trained on responses <= cutoff only. This harness cannot enforce that (it
    only reads the stored queue); the orchestrator owns that discipline. Using
    `--baseline` reuses the *exact* same held-out set across BEFORE/AFTER so the
    comparison is apples-to-apples regardless.

USAGE
-----
    # baseline scoreboard over the live queues (read-only)
    python3 scripts/archive/eval_recommender.py

    # snapshot BEFORE a rebuild, then diff AFTER (orchestrator gate)
    python3 scripts/archive/eval_recommender.py --out state/archive/_tmp/eval_before.json
    # ... operator rebuilds queues ...
    python3 scripts/archive/eval_recommender.py \
        --baseline state/archive/_tmp/eval_before.json \
        --out state/archive/_tmp/eval_after.json
    #   -> prints per-researcher BEFORE/AFTER; exits 3 if a gate target regresses

Exit codes: 0 ok · 2 usage/data error · 3 gate regression (only with --baseline).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

from _common import same_work, dedup_same_work, _cmp_doi  # noqa: E402

POS_CHOICES = ("save_later", "already_read")
NEG_CHOICES = ("not_relevant",)
LABELED_CHOICES = POS_CHOICES + NEG_CHOICES
DEFAULT_KS = (5, 20, 50)
DEFAULT_GATE_TARGETS = ("BHL", "JYK", "SMJ")
CHUNK_PRI = {"recent": 0, "mid": 1, "classic": 2}


# --------------------------------------------------------------------------- #
# DB access (read-only) — one lazy handle shared by CLI and validate_drift.
# --------------------------------------------------------------------------- #

def _db():
    """Return (query_json, ledger_schema_name) after loading .env. Read-only."""
    from _db import load_env, query_json, ledger_schema  # noqa: E402
    load_env()
    return query_json, ledger_schema()


def _q_lit(s: str) -> str:
    """Single-quote-escape a value for inline SQL (identifiers/inits only)."""
    return "'" + str(s).replace("'", "''") + "'"


def _fetch_responder_inits(query_json, sch: str) -> list[str]:
    rows = query_json(
        f"SELECT DISTINCT researcher_id AS init "
        f"FROM {sch}.archive_responses ORDER BY researcher_id"
    )
    return [r["init"] for r in rows if r.get("init")]


def _fetch_responses(query_json, sch: str, init: str) -> list[dict]:
    """Labeled responses for one researcher, newest-comparable via epoch.

    responded_at is stored as text with mixed tz spellings (space vs 'T',
    '+00' vs '+00:00'), and built_at is KST while responded_at is UTC — so we
    let Postgres do the time math (EXTRACT EPOCH FROM ::timestamptz) instead of
    string-comparing, which silently mis-orders across tz offsets.
    """
    rows = query_json(f"""
        SELECT r.canonical_id,
               p.doi,
               p.title_norm,
               r.choice,
               EXTRACT(EPOCH FROM r.responded_at::timestamptz) AS ts
          FROM {sch}.archive_responses r
          LEFT JOIN {sch}.archive_papers p USING (canonical_id)
         WHERE r.researcher_id = {_q_lit(init)}
    """)
    out = []
    for r in rows:
        if r.get("ts") is None:
            continue
        out.append({
            "canonical_id": r["canonical_id"],
            "doi": r.get("doi"),
            "title_norm": r.get("title_norm") or "",
            "choice": r["choice"],
            "ts": float(r["ts"]),
        })
    return out


def _fetch_queue_db(query_json, sch: str, init: str) -> list[dict]:
    rows = query_json(f"""
        SELECT q.canonical_id,
               p.doi,
               p.title_norm,
               q.chunk,
               q.rank_in_chunk,
               q.similarity,
               q.composite
          FROM {sch}.archive_researcher_queues q
          LEFT JOIN {sch}.archive_papers p USING (canonical_id)
         WHERE q.researcher_id = {_q_lit(init)}
    """)
    out = []
    for r in rows:
        out.append({
            "canonical_id": r["canonical_id"],
            "doi": r.get("doi"),
            "title_norm": r.get("title_norm") or "",
            "chunk": r.get("chunk"),
            "rank_in_chunk": r.get("rank_in_chunk"),
            "similarity": _as_float(r.get("similarity")),
            "composite": _as_float(r.get("composite")),
        })
    return out


def _load_queue_jsonl(path: Path) -> dict[str, list[dict]]:
    """Score an offline (not-yet-applied) queue. One JSON object per line with
    at least researcher_id + canonical_id; doi/title_norm/composite/similarity/
    chunk/rank_in_chunk optional. Lets an orchestrator score a candidate
    rebuild before applying it to prod."""
    by_init: dict[str, list[dict]] = {}
    for ln in path.read_text("utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        r = json.loads(ln)
        init = (r.get("researcher_id") or r.get("init") or "").strip().upper()
        if not init or not r.get("canonical_id"):
            continue
        by_init.setdefault(init, []).append({
            "canonical_id": r["canonical_id"],
            "doi": r.get("doi"),
            "title_norm": r.get("title_norm") or "",
            "chunk": r.get("chunk"),
            "rank_in_chunk": r.get("rank_in_chunk"),
            "similarity": _as_float(r.get("similarity")),
            "composite": _as_float(r.get("composite")),
        })
    return by_init


def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Ranking + matching
# --------------------------------------------------------------------------- #

def _rank_queue(records: list[dict], order: str) -> list[dict]:
    """Dedup twins (max-composite representative) then assign a 1-based global
    rank. Dedup means a preprint<->published twin occupies ONE slot, so a
    held-out paper can neither be double-credited nor create a phantom miss."""
    survivors = dedup_same_work(list(records), composite_key="composite")

    def comp_key(r):
        c = r.get("composite")
        s = r.get("similarity")
        return (
            -(c if c is not None else float("-inf")),
            -(s if s is not None else float("-inf")),
            CHUNK_PRI.get(r.get("chunk"), 9),
            r.get("rank_in_chunk") if r.get("rank_in_chunk") is not None else 10**9,
        )

    def pres_key(r):
        return (
            CHUNK_PRI.get(r.get("chunk"), 9),
            r.get("rank_in_chunk") if r.get("rank_in_chunk") is not None else 10**9,
            -(r.get("composite") if r.get("composite") is not None else float("-inf")),
        )

    key = pres_key if order == "presentation" else comp_key
    survivors = sorted(survivors, key=key)
    for i, r in enumerate(survivors, start=1):
        r["_rank"] = i
    return survivors


def _real_doi(rec: dict) -> Optional[str]:
    d = _cmp_doi(rec.get("doi"))
    if d is None or d.startswith("arxiv:"):
        return None
    return d


def _best_rank(pos: dict, survivors: list[dict], by_cid: dict, by_doi: dict,
               fuzz: int) -> Optional[int]:
    """Best (lowest) queue rank matching this held-out paper. Exact canonical_id
    or real-DOI first (cheap); else a same_work() fuzzy scan (twin-tolerant)."""
    r = by_cid.get(pos["canonical_id"])
    if r is not None:
        return r
    d = _real_doi(pos)
    if d is not None and d in by_doi:
        return by_doi[d]
    best = None
    for s in survivors:  # already rank-ordered
        if same_work(pos, s, fuzz=fuzz):
            best = s["_rank"]
            break
    return best


# --------------------------------------------------------------------------- #
# Per-researcher compute
# --------------------------------------------------------------------------- #

def compute_one(init: str, responses: list[dict], queue: list[dict], *,
                holdout_frac: float, cutoff_epoch: Optional[float],
                order: str, ks: tuple[int, ...], fuzz: int,
                reuse_pos: Optional[list[dict]] = None,
                reuse_neg: Optional[list[dict]] = None) -> dict:
    """Return a metrics dict for one researcher. Pure function of its inputs."""
    labeled = [r for r in responses if r["choice"] in LABELED_CHOICES]

    # All-time baseline precision = save/(save+not_relevant) — the live
    # validate_drift number, recomputed here (never hard-coded).
    n_save_all = sum(1 for r in responses if r["choice"] == "save_later")
    n_neg_all = sum(1 for r in responses if r["choice"] == "not_relevant")
    baseline_precision = (n_save_all / (n_save_all + n_neg_all)
                          if (n_save_all + n_neg_all) else None)

    # ----- hold-out split ------------------------------------------------- #
    if reuse_pos is not None:
        # BEFORE/AFTER: reuse the baseline's exact held-out records verbatim.
        hold_pos = _dedup_papers(reuse_pos)
        hold_neg = _dedup_papers(reuse_neg or [])
        n_train = len(labeled) - (len(reuse_pos) + len(reuse_neg or []))
        cutoff_used = cutoff_epoch
    else:
        labeled_sorted = sorted(labeled, key=lambda r: (r["ts"], r["canonical_id"] or ""))
        if cutoff_epoch is not None:
            hold = [r for r in labeled_sorted if r["ts"] > cutoff_epoch]
            train = [r for r in labeled_sorted if r["ts"] <= cutoff_epoch]
            cutoff_used = cutoff_epoch
        else:
            n = len(labeled_sorted)
            n_hold = max(1, round(holdout_frac * n)) if n >= 2 else 0
            n_hold = min(n_hold, n - 1) if n >= 2 else 0
            split = n - n_hold
            train = labeled_sorted[:split]
            hold = labeled_sorted[split:]
            cutoff_used = (train[-1]["ts"] if train else
                           (hold[0]["ts"] if hold else None))
        n_train = len(train)
        hold_pos = _dedup_papers([r for r in hold if r["choice"] in POS_CHOICES])
        hold_neg = _dedup_papers([r for r in hold if r["choice"] in NEG_CHOICES])

    # ----- rank the queue ------------------------------------------------- #
    survivors = _rank_queue(queue, order)
    by_cid = {s["canonical_id"]: s["_rank"] for s in survivors}
    by_doi: dict[str, int] = {}
    for s in survivors:
        d = _real_doi(s)
        if d is not None and d not in by_doi:
            by_doi[d] = s["_rank"]

    n_q = len(survivors)
    worst = n_q + 1

    pos_ranks = [_best_rank(p, survivors, by_cid, by_doi, fuzz) for p in hold_pos]
    neg_ranks = [_best_rank(p, survivors, by_cid, by_doi, fuzz) for p in hold_neg]
    n_pos = len(hold_pos)
    matched_pos = sum(1 for r in pos_ranks if r is not None)

    def recall_at(k: int) -> Optional[float]:
        if n_pos == 0:
            return None
        return sum(1 for r in pos_ranks if r is not None and r <= k) / n_pos

    mrr = (sum((1.0 / r) for r in pos_ranks if r is not None) / n_pos
           if n_pos else None)

    # precision@5: of the top-5 deduped queue works, how many are a held-out
    # positive (twin-tolerant). Dedup guarantees each work counts once.
    top5 = survivors[:5]
    pos_cids = {p["canonical_id"] for p in hold_pos}
    pos_dois = {d for d in (_real_doi(p) for p in hold_pos) if d}
    hits5 = 0
    for s in top5:
        if s["canonical_id"] in pos_cids or (_real_doi(s) in pos_dois if _real_doi(s) else False):
            hits5 += 1
            continue
        if any(same_work(s, p, fuzz=fuzz) for p in hold_pos):
            hits5 += 1
    precision_at_5 = (hits5 / min(5, n_q)) if n_q else None

    # temporal held-out precision (label-only analog of validate_drift precision)
    held_out_precision = (n_pos / (n_pos + len(hold_neg))
                          if (n_pos + len(hold_neg)) else None)

    # aux: pos-vs-neg rank AUC (uses the not_relevant labels). Unmatched -> worst.
    auc = None
    if n_pos and hold_neg:
        pr = [(r if r is not None else worst) for r in pos_ranks]
        nr = [(r if r is not None else worst) for r in neg_ranks]
        wins = ties = 0
        for a in pr:
            for b in nr:
                if a < b:
                    wins += 1
                elif a == b:
                    ties += 1
        tot = len(pr) * len(nr)
        auc = (wins + 0.5 * ties) / tot if tot else None

    return {
        "init": init,
        "n_labeled": len(labeled),
        "n_train": n_train,
        "n_holdout_pos": n_pos,
        "n_holdout_neg": len(hold_neg),
        "queue_size": n_q,
        "matched_pos": matched_pos,
        "cutoff_epoch": cutoff_used,
        "cutoff_iso": (datetime.fromtimestamp(cutoff_used, tz=timezone.utc).isoformat()
                       if cutoff_used is not None else None),
        "baseline_precision": baseline_precision,
        "held_out_precision": held_out_precision,
        "recall": {str(k): recall_at(k) for k in ks},
        "mrr": mrr,
        "precision_at_5": precision_at_5,
        "auc_pos_vs_neg": auc,
        "holdout_pos_ids": [{"canonical_id": p["canonical_id"], "doi": p.get("doi"),
                             "title_norm": p.get("title_norm") or ""} for p in hold_pos],
        "holdout_neg_ids": [{"canonical_id": p["canonical_id"], "doi": p.get("doi"),
                             "title_norm": p.get("title_norm") or ""} for p in hold_neg],
    }


def _dedup_papers(recs: list[dict]) -> list[dict]:
    """Collapse twin held-out papers so one work == one relevant item."""
    for r in recs:
        r.setdefault("composite", 0.0)
    return dedup_same_work(list(recs), composite_key="composite")


# --------------------------------------------------------------------------- #
# Scoreboard orchestration
# --------------------------------------------------------------------------- #

def compute_scoreboard(inits: Optional[list[str]] = None, *,
                       holdout_frac: float = 0.3,
                       cutoff: Optional[str] = None,
                       order: str = "composite",
                       ks: tuple[int, ...] = DEFAULT_KS,
                       fuzz: int = 92,
                       queue_source: str = "db",
                       baseline: Optional[dict] = None) -> dict[str, dict]:
    """Read-only. Returns {init: metrics}. `baseline` (a prior snapshot dict)
    reuses each researcher's exact held-out set for an apples-to-apples diff."""
    query_json, sch = _db()
    if inits is None:
        inits = _fetch_responder_inits(query_json, sch)
    inits = [i.strip().upper() for i in inits]

    cutoff_epoch = _parse_cutoff(cutoff)

    queues_jsonl = None
    if queue_source not in ("db", "", None):
        queues_jsonl = _load_queue_jsonl(Path(queue_source))

    base_res = (baseline or {}).get("researchers", {})
    board: dict[str, dict] = {}
    for init in inits:
        responses = _fetch_responses(query_json, sch, init)
        if queues_jsonl is not None:
            queue = queues_jsonl.get(init, [])
        else:
            queue = _fetch_queue_db(query_json, sch, init)
        reuse_pos = reuse_neg = None
        b = base_res.get(init)
        if b is not None:
            reuse_pos = b.get("holdout_pos_ids") or []
            reuse_neg = b.get("holdout_neg_ids") or []
        board[init] = compute_one(
            init, responses, queue,
            holdout_frac=holdout_frac, cutoff_epoch=cutoff_epoch,
            order=order, ks=ks, fuzz=fuzz,
            reuse_pos=reuse_pos, reuse_neg=reuse_neg,
        )
    return board


def _parse_cutoff(cutoff: Optional[str]) -> Optional[float]:
    if not cutoff:
        return None
    s = cutoff.strip().replace(" ", "T")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # date-only
        dt = datetime.fromisoformat(s + "T00:00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _pct(x) -> str:
    return f"{x:.0%}" if isinstance(x, (int, float)) else "—"


def _num(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "—"


def print_scoreboard(board: dict[str, dict], *, ks: tuple[int, ...] = DEFAULT_KS,
                     order: str = "composite") -> None:
    hdr = (f"{'init':5s} {'lbl':>4s} {'trn':>4s} {'h+':>3s} {'h-':>3s} "
           f"{'qN':>4s} {'base':>5s} {'hpre':>5s} "
           + " ".join(f"R@{k:<3d}".rjust(6) for k in ks)
           + f" {'MRR':>5s} {'P@5':>5s} {'AUC':>5s}")
    print(f"# held-out recommender eval  (order={order}; base=all-time "
          f"save/(save+not_rel); hpre=held-out precision)")
    print(hdr)
    print("-" * len(hdr))
    for init in sorted(board):
        m = board[init]
        rec = " ".join(_pct(m["recall"].get(str(k))).rjust(6) for k in ks)
        print(f"{init:5s} {m['n_labeled']:>4d} {m['n_train']:>4d} "
              f"{m['n_holdout_pos']:>3d} {m['n_holdout_neg']:>3d} "
              f"{m['queue_size']:>4d} {_pct(m['baseline_precision']):>5s} "
              f"{_pct(m['held_out_precision']):>5s} {rec} "
              f"{_num(m['mrr']):>5s} {_pct(m['precision_at_5']):>5s} "
              f"{_num(m['auc_pos_vs_neg']):>5s}")


def diff_and_gate(baseline: dict, current: dict[str, dict], *,
                  targets: tuple[str, ...], gate_k: int, eps: float) -> bool:
    """Print BEFORE/AFTER recall@gate_k. Return True iff a gate target regressed
    (recall dropped by more than eps). None-vs-value transitions: a target whose
    held-out set is empty is skipped (undefined, not a regression)."""
    base_res = baseline.get("researchers", {})
    k = str(gate_k)
    print(f"\n# BEFORE/AFTER diff  (gate = recall@{gate_k} for "
          f"{{{','.join(targets)}}} must not regress by > {eps:g})")
    hdr = f"{'init':5s} {'before':>7s} {'after':>7s} {'delta':>7s}  {'gate':>4s}"
    print(hdr)
    print("-" * len(hdr))
    regressed = False
    for init in sorted(set(base_res) | set(current)):
        b = base_res.get(init, {})
        a = current.get(init, {})
        rb = (b.get("recall") or {}).get(k)
        ra = (a.get("recall") or {}).get(k)
        is_target = init in targets
        mark = ""
        if is_target and rb is not None and ra is not None:
            delta = ra - rb
            if delta < -eps:
                regressed = True
                mark = "FAIL"
            elif delta > eps:
                mark = "up"
            else:
                mark = "ok"
        elif is_target:
            mark = "n/a"
        delta_s = (_pct(ra - rb) if (rb is not None and ra is not None) else "—")
        print(f"{init:5s} {_pct(rb):>7s} {_pct(ra):>7s} {delta_s:>7s}  {mark:>4s}")
    return regressed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _snapshot(board: dict[str, dict], params: dict) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": params,
        "researchers": board,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("researcher", nargs="?", default=None,
                    help="single init; default = all researchers with responses")
    ap.add_argument("--researchers", default=None,
                    help="comma-separated inits (overrides positional)")
    ap.add_argument("--holdout-frac", type=float, default=0.3,
                    help="fraction of each researcher's newest labels held out (default 0.3)")
    ap.add_argument("--cutoff", default=None,
                    help="fixed ISO date/datetime split (overrides --holdout-frac); "
                         "held-out = responded strictly after cutoff")
    ap.add_argument("--order", choices=("composite", "presentation"),
                    default="composite",
                    help="composite = model-score global order (default); "
                         "presentation = deployed chunk->rank_in_chunk order")
    ap.add_argument("--ks", default="5,20,50", help="recall cutoffs (default 5,20,50)")
    ap.add_argument("--fuzz", type=int, default=92, help="same_work title fuzz (default 92)")
    ap.add_argument("--queue-source", default="db",
                    help="'db' (live archive_researcher_queues) or a JSONL path to "
                         "score an offline/not-yet-applied queue")
    ap.add_argument("--out", default=None, help="write snapshot JSON here")
    ap.add_argument("--baseline", default=None,
                    help="prior snapshot JSON: reuse its held-out sets + print "
                         "BEFORE/AFTER diff and gate the exit code")
    ap.add_argument("--gate-targets", default=",".join(DEFAULT_GATE_TARGETS),
                    help="comma inits gated for non-regression (default BHL,JYK,SMJ)")
    ap.add_argument("--gate-k", type=int, default=50, help="recall@k gated (default 50)")
    ap.add_argument("--regress-eps", type=float, default=0.0,
                    help="allowed recall@k drop before FAIL (default 0.0 = strict)")
    ap.add_argument("--json", action="store_true", help="dump raw metrics JSON to stdout")
    args = ap.parse_args(argv)

    if args.researchers:
        inits = [x.strip().upper() for x in args.researchers.split(",") if x.strip()]
    elif args.researcher:
        inits = [args.researcher.strip().upper()]
    else:
        inits = None
    ks = tuple(int(x) for x in str(args.ks).split(",") if x.strip())
    targets = tuple(x.strip().upper() for x in args.gate_targets.split(",") if x.strip())

    baseline = None
    if args.baseline:
        bp = Path(args.baseline)
        if not bp.exists():
            print(f"error: --baseline {bp} not found", file=sys.stderr)
            return 2
        baseline = json.loads(bp.read_text("utf-8"))

    try:
        board = compute_scoreboard(
            inits, holdout_frac=args.holdout_frac, cutoff=args.cutoff,
            order=args.order, ks=ks, fuzz=args.fuzz,
            queue_source=args.queue_source, baseline=baseline,
        )
    except Exception as e:  # surface DB/parse errors cleanly, never write
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_snapshot(board, vars(args)), indent=2, ensure_ascii=False))
    else:
        print_scoreboard(board, ks=ks, order=args.order)

    rc = 0
    if baseline is not None:
        regressed = diff_and_gate(baseline, board, targets=targets,
                                  gate_k=args.gate_k, eps=args.regress_eps)
        rc = 3 if regressed else 0
        print(f"\n# gate: {'REGRESSION' if regressed else 'PASS'} "
              f"(recall@{args.gate_k}, targets={{{','.join(targets)}}})")

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(_snapshot(board, vars(args)), indent=2,
                                   ensure_ascii=False), "utf-8")
        print(f"\nsnapshot -> {outp}", file=sys.stderr)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
