#!/usr/bin/env python3
"""
scripts/archive/cold_start_msy.py — cold-start MSY's P28 survey memory.

WHY
  MSY is the one researcher whose v13 profile survey is the BLANK template
  (0 `(직접 작성)` edits beyond the scaffold, 0 interview responses — never
  onboarded). ingest_survey therefore parses 0 aims for her, and
  recommend.py's connection engine explicitly SKIPS a researcher with no aims
  ("legacy builder owns this researcher") — so MSY silently falls back to the
  TF-IDF-salvage fingerprint path while every other researcher gets the
  connection-based recommender.

  But a real, coherent profile for MSY DOES exist outside the survey:
    * csnl_research.projects (init='MSY') — TWO live projects (cat_mag_main,
      face_cond_ver10), each grounded in NAS + the round2 ingest ledger:
      task-dependent generative models, serial dependence / history effects in
      face-gender judgments, StyleGAN2 gender-degree face stimuli, online +
      in-lab behaviour, Bayesian modelling of the decision variable.
    * csnl-ops/docs/researcher_summaries/MSY.md — the ops record (now aged-out
      to "Unknown / open questions", but preserving the same phenomenon/task/
      method tuple + the Crossref-verified seed paper Ranieri 2025, BMC Biology).
    * state/archive/_explore/batch04/O1-researchers.md — the batch04 synthesis
      that flagged this as "the single highest-value import".

  This script emits a hand-curated COLD-START seed of archive_survey_aims /
  _keywords / _methods rows for MSY so the P28 connection engine runs for her
  TODAY instead of the legacy fallback — without waiting on onboarding.

HONESTY / CONFIDENCE (this is a seed, not researcher truth)
  Every row is written with confidence='low' and source_version=
  'ops-summary@cold-start'. MSY has NOT self-authored or verified any of this
  via the survey or an interview, so:
    * These aims are DEPRIORITISE-grade priors that merely let the engine run —
      NOT high-confidence anchors.
    * NO archive_survey_negatives are emitted. Fabricating a veto for a
      researcher with zero response history is exactly the P24 landmine
      (never veto on an inferred/shared signal). Her negatives stay empty until
      she interviews.
    * NO archive_survey_profile write — a blank-survey profile row already
      exists (P28 ingest) and recommend.py tolerates it; the summary is not
      re-fabricated here.
  raw_jsonb on every row records per-field grounding (live csnl_research vs
  aged ops-summary vs cross-project inference) so an audit can trace it.

BOUNDARY
  * DEFAULT = DRY-RUN: print the exact rows + an offline "will-it-admit"
    phrase preview; write NOTHING.
  * --check-live: READ-ONLY SELECT of csnl_research.projects (init='MSY') to
    confirm the seed still matches the live projects, plus current MSY row
    counts in archive_survey_*. Never writes. Skipped gracefully if the DB is
    unreachable.
  * --apply: transactional UPSERT of ONLY the three tables, ONLY for MSY
    (DELETE researcher_id='MSY' then INSERT). Touches no other researcher, no
    other table, never csnl_research, never archive_responses. Operator-gated
    (`!`) — the autonomous builder pass does NOT run it.

CLI
  python3 scripts/archive/cold_start_msy.py                 # DRY-RUN (offline)
  python3 scripts/archive/cold_start_msy.py --json          # structured dump
  python3 scripts/archive/cold_start_msy.py --check-live     # read-only cross-check
  ! python3 scripts/archive/cold_start_msy.py --apply        # operator, MSY-only write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))

# ---- invariants (belt-and-braces: this script is MSY-only, 3 tables only) ----
RID = "MSY"
SOURCE_VERSION = "ops-summary@cold-start"
CONFIDENCE = "low"
TARGET_TABLES = (
    "archive_survey_aims",
    "archive_survey_keywords",
    "archive_survey_methods",
)

# ===========================================================================
# Curated cold-start seed.
#
# Grounding tags used in raw_jsonb:
#   live   = csnl_research.projects (init='MSY') — current, NAS/ledger-grounded
#   ops    = csnl-ops/docs/researcher_summaries/MSY.md — aged-out ops record
#   o1     = state/archive/_explore/batch04/O1-researchers.md — batch04 synthesis
#   infer  = cross-project / connective inference (weakest)
# ===========================================================================

COLD_START_AIMS: list[dict] = [
    {
        "aim_id": "P1",
        "aim_label": "cat_mag_main",
        "hyp_type": "유형1",
        # anchors ---------------------------------------------------------
        "domain": "face perception; face-gender categorization",
        "phenomenon": ("serial dependence / history effects in face-gender "
                       "judgments; task-dependent history bias"),
        "task": ("in-lab within-subject categorization vs. magnitude judgment "
                 "of StyleGAN2 gender-degree faces (4-session)"),
        "mechanism": ("task structure recruits different generative models, "
                      "restructuring prior/likelihood; Bayesian updating of the "
                      "decision variable"),
        # priority signal — NEVER a veto ----------------------------------
        "population": "neurotypical human adults",
        # rerankers -------------------------------------------------------
        "metric": ("history-effect / serial-dependence bias "
                   "(scaled Gaussian fit, lag-based regression)"),
        "condition": "categorical vs. magnitude task structure on identical stimuli",
        "direction": "",
        "hypothesis": ("Categorical vs. magnitude task structure yields different "
                       "generative models, differentiating history-effect patterns "
                       "for identical stimuli."),
        "background": ("Taubert & Burr 2016 (face serial-dependence baseline); "
                       "Bernardi & Salzman 2020 (task-dependent abstract "
                       "representation geometry); Polania 2019 (value/magnitude "
                       "normative framing)."),
        "seed_paper": ("Ranieri et al. (2025), BMC Biology — serial dependence in "
                       "face-gender (Crossref-verified)."),
        "measures": {"behavior": True},
        "raw_jsonb": {
            "grounding": {
                "domain": "live", "phenomenon": "live+ops+o1", "task": "live",
                "mechanism": "live", "population": "live(priority-not-veto)",
                "metric": "live", "condition": "live", "hypothesis": "live",
                "background": "live(prior_studies)", "seed_paper": "o1+ops",
            },
            "live_slug": "cat_mag_main",
            "live_phase": "analysis",
            "note": ("COLD-START seed (confidence=low): MSY has no self-authored "
                     "survey and 0 interview responses. Deprioritise-grade prior "
                     "to let the P28 engine run; NOT a veto source (P24)."),
        },
    },
    {
        "aim_id": "P2",
        "aim_label": "face_cond_ver10",
        "hyp_type": "유형1",
        "domain": "face perception; online psychophysics",
        "phenomenon": ("task-dependent history effect / serial dependence in "
                       "face-gender judgments (online large-N generalization)"),
        "task": ("online single-session categorization/magnitude judgment of "
                 "StyleGAN2 gender-degree faces (Prolific)"),
        "mechanism": ("task-dependent generative model; prior/likelihood "
                      "restructuring across task; history features from the "
                      "previous stimulus/response"),
        "population": "neurotypical human adults (Prolific online crowdsourcing)",
        "metric": ("history-effect bias from previous-trial features "
                   "(stim_prev, resp_prev, RT_prev)"),
        "condition": "in-lab (cat_mag_main) vs. online replication",
        "direction": "",
        "hypothesis": ("The task-dependent generative-model account of history "
                       "effects generalizes to online single-session settings "
                       "(external validity of cat_mag_main)."),
        "background": ("Direct online extension of cat_mag_main; Taubert & Burr "
                       "2016 (face serial dependence)."),
        "seed_paper": "Taubert & Burr (2016) — face serial dependence.",
        "measures": {"behavior": True},
        "raw_jsonb": {
            "grounding": {
                "domain": "live", "phenomenon": "live", "task": "live",
                "mechanism": "live", "population": "live(priority-not-veto)",
                "metric": "live", "condition": "live", "hypothesis": "live",
                "background": "live", "seed_paper": "live(prior_studies)",
            },
            "live_slug": "face_cond_ver10",
            "live_phase": "data_collection",
            "note": ("COLD-START seed (confidence=low). Shares the core phenomenon "
                     "with P1 (task-dependent history effect); online arc."),
        },
    },
]

# Keyword phrases are kept SPECIFIC (multiword where possible) to avoid the P28
# C1 landmine (generic-unigram over-firing). Two ambiguous terms carry an
# operational_def + conflict_term so P28's definition-aware subtraction can
# down-weight the wrong sense (memory/sequence-learning "serial dependence" etc).
COLD_START_KEYWORDS: list[dict] = [
    {
        "keyword": "serial dependence",
        "is_ambiguous": True,
        "operational_def": ("attractive bias of the current face-gender judgment "
                            "toward recent stimuli/responses (a trial-history effect)"),
        "conflict_term": "serial-order / sequence memory or sequence learning",
        "raw_jsonb": {"grounding": "live+ops"},
    },
    {
        "keyword": "history effect",
        "is_ambiguous": True,
        "operational_def": ("dependence of the current response on the previous-"
                            "trial stimulus/response (gambler's-fallacy / attractive bias)"),
        "conflict_term": "developmental/learning history or clinical case history",
        "raw_jsonb": {"grounding": "live"},
    },
    {"keyword": "task-dependent generative model",
     "raw_jsonb": {"grounding": "live"}},
    {"keyword": "categorization vs magnitude decision",
     "raw_jsonb": {"grounding": "live"}},
    {"keyword": "Bayesian updating",
     "raw_jsonb": {"grounding": "live(researcher_view)"}},
    {"keyword": "decision variable",
     "raw_jsonb": {"grounding": "live(researcher_view)"}},
    {"keyword": "gambler's fallacy",
     "raw_jsonb": {"grounding": "live(researcher_view)"}},
    {"keyword": "StyleGAN2 face generation",
     "raw_jsonb": {"grounding": "live+ops(딥러닝 얼굴이미지 생성)"}},
    {"keyword": "face gender judgment",
     "raw_jsonb": {"grounding": "live"}},
    {"keyword": "prior likelihood structure",
     "raw_jsonb": {"grounding": "live(scientific_aim)"}},
]

# archive_survey_methods: (modality ∈ behavior|eye|neural|ann) × approach.
# The recommender's _method_signal turns `approach` into phrases (a WEAK
# reranker), so approaches are specific. All combos are PK-distinct.
COLD_START_METHODS: list[dict] = [
    {"modality": "behavior", "approach": "online psychophysics",
     "raw_label": "온라인 행동실험 — Prolific 온라인 크라우드소싱 (PsychoJS)"},
    {"modality": "behavior", "approach": "Bayesian modeling",
     "raw_label": "Bayesian modeling of the decision variable (prior/likelihood restructuring)"},
    {"modality": "behavior", "approach": "history-effect curve fitting",
     "raw_label": ("scaled Gaussian fit + lag-based history-effect regression "
                   "(LinearRegression / LogisticRegression / GaussianMixture / PCA)")},
    {"modality": "ann", "approach": "deep generative face synthesis",
     "raw_label": "딥러닝 얼굴이미지 생성 — StyleGAN2 semantic-factorization gender-degree faces"},
]


# ===========================================================================
# Offline "will-it-admit" phrase preview
#
# A dependency-free approximation of recommend._phrases: it shows the SPECIFIC
# (multiword) phrases each aim's phenomenon yields, so the dry-run demonstrates
# the P28 engine can admit MSY on axis B (a specific multiword phenomenon match
# at ≥ STRONG_CONN is the auto-admit path). This mirrors the tokenizer but is
# intentionally standalone so the script stays offline and import-light.
# ===========================================================================

_CLAUSE_SPLIT = re.compile(r"[/·,;:|()→\[\]{}]+|\bvs\b|\b및\b|\b또는\b")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z가-힣\s]", " ", (s or "").lower())).strip()


def _multiword_phrases(text: str) -> list[str]:
    out: list[str] = []
    for part in _CLAUSE_SPLIT.split(text or ""):
        toks = [w for w in _norm(part).split() if len(w) > 1]
        for n in (3, 2):
            for i in range(len(toks) - n + 1):
                out.append(" ".join(toks[i:i + n]))
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ===========================================================================
# Dry-run printing
# ===========================================================================

def _print_seed() -> None:
    print(f"\n=== COLD-START seed for {RID} "
          f"(source_version={SOURCE_VERSION!r}, confidence={CONFIDENCE!r}) ===")
    print(f"  tables: {', '.join(TARGET_TABLES)}")
    print(f"  negatives: 0 (intentional — no fabricated veto; P24 landmine)")
    print(f"  profile:   not written (blank-survey row already exists; tolerated)\n")

    print(f"  aims: {len(COLD_START_AIMS)}")
    for a in COLD_START_AIMS:
        print(f"    {a['aim_id']} [{a['hyp_type']}] {a['aim_label']} (conf={CONFIDENCE})")
        print(f"       domain     = {a['domain']!r}")
        print(f"       phenomenon = {a['phenomenon']!r}")
        print(f"       task       = {a['task']!r}")
        print(f"       mechanism  = {a['mechanism']!r}")
        print(f"       population = {a['population']!r}  (priority signal, NOT a veto)")
        print(f"       seed_paper = {a['seed_paper']!r}")
        specB = _multiword_phrases(a["phenomenon"])
        print(f"       axis-B specific phrases ({len(specB)}): "
              f"{', '.join(specB[:8])}{' …' if len(specB) > 8 else ''}")
        if not specB:
            print("       ⚠ phenomenon yields NO multiword phrase — engine could not "
                  "auto-admit on axis B; fix the phrasing.")

    print(f"\n  keywords: {len(COLD_START_KEYWORDS)} "
          f"({sum(1 for k in COLD_START_KEYWORDS if k.get('is_ambiguous'))} ambiguous w/ def)")
    for k in COLD_START_KEYWORDS:
        amb = "  [def-aware]" if k.get("is_ambiguous") else ""
        print(f"    - {k['keyword']}{amb}")
        if k.get("is_ambiguous"):
            print(f"        def={k['operational_def']!r}")
            print(f"        not={k['conflict_term']!r}")

    print(f"\n  methods: {len(COLD_START_METHODS)}")
    for m in COLD_START_METHODS:
        print(f"    - [{m['modality']}] {m['approach']}  ({m['raw_label']})")

    issues = validate()
    if issues:
        print("\n  ‼ validation issues:")
        for i in issues:
            print(f"      - {i}")
    else:
        print("\n  ✓ validation clean (every aim has domain+phenomenon+task; "
              "every phenomenon yields ≥1 specific phrase; PKs unique).")


def _structured_seed() -> dict:
    def _aim_row(a: dict) -> dict:
        return {
            "researcher_id": RID, "aim_id": a["aim_id"], "aim_label": a["aim_label"],
            "hyp_type": a["hyp_type"], "domain": a["domain"],
            "phenomenon": a["phenomenon"], "task": a["task"],
            "mechanism": a["mechanism"], "population": a["population"],
            "metric": a["metric"], "condition": a["condition"],
            "direction": a["direction"], "hypothesis": a["hypothesis"],
            "background": a["background"], "seed_paper": a["seed_paper"],
            "measures": a["measures"], "confidence": CONFIDENCE,
            "source_version": SOURCE_VERSION, "raw_jsonb": a["raw_jsonb"],
        }

    def _kw_row(k: dict) -> dict:
        return {
            "researcher_id": RID, "keyword": k["keyword"],
            "is_ambiguous": bool(k.get("is_ambiguous")),
            "operational_def": k.get("operational_def"),
            "conflict_term": k.get("conflict_term"), "bound_aim": None,
            "source_version": SOURCE_VERSION, "raw_jsonb": k.get("raw_jsonb") or {},
        }

    def _m_row(m: dict) -> dict:
        return {
            "researcher_id": RID, "modality": m["modality"],
            "approach": m["approach"], "raw_label": m["raw_label"],
            "source_version": SOURCE_VERSION,
        }

    return {
        "researcher_id": RID, "source_version": SOURCE_VERSION,
        "confidence": CONFIDENCE,
        "aims": [_aim_row(a) for a in COLD_START_AIMS],
        "keywords": [_kw_row(k) for k in COLD_START_KEYWORDS],
        "methods": [_m_row(m) for m in COLD_START_METHODS],
        "negatives": [], "profile": None,
    }


# ===========================================================================
# Validation
# ===========================================================================

def validate() -> list[str]:
    issues: list[str] = []
    # aim PK + anchor completeness
    seen_aim: set[str] = set()
    for a in COLD_START_AIMS:
        if a["aim_id"] in seen_aim:
            issues.append(f"duplicate aim_id {a['aim_id']}")
        seen_aim.add(a["aim_id"])
        for col in ("domain", "phenomenon", "task"):
            if not (a.get(col) or "").strip():
                issues.append(f"{a['aim_id']}: anchor '{col}' blank")
        if not _multiword_phrases(a["phenomenon"]):
            issues.append(f"{a['aim_id']}: phenomenon yields no multiword phrase")
    # keyword PK
    seen_kw: set[str] = set()
    for k in COLD_START_KEYWORDS:
        key = k["keyword"].lower()
        if key in seen_kw:
            issues.append(f"duplicate keyword {k['keyword']!r}")
        seen_kw.add(key)
    # method PK
    seen_m: set[tuple[str, str]] = set()
    for m in COLD_START_METHODS:
        key = (m["modality"], m["approach"])
        if key in seen_m:
            issues.append(f"duplicate method {key}")
        seen_m.add(key)
        if m["modality"] not in ("behavior", "eye", "neural", "ann"):
            issues.append(f"method modality {m['modality']!r} not in enum")
    return issues


# ===========================================================================
# --check-live : READ-ONLY cross-check against csnl_research + existing rows
# ===========================================================================

def check_live() -> int:
    try:
        from _db import load_env, query_json, ledger_schema  # noqa: E402
    except Exception as e:  # pragma: no cover
        print(f"[check-live] _db import failed ({e}); skipping (offline-safe).",
              file=sys.stderr)
        return 0
    try:
        load_env()
        sch = ledger_schema()
        print("\n=== live cross-check (READ-ONLY) ===")
        rows = query_json(
            "SELECT project_slug, title, phase, "
            "purpose_jsonb->>'hypothesis' AS hypothesis, "
            "purpose_jsonb->>'scientific_aim' AS scientific_aim "
            "FROM csnl_research.projects WHERE init='MSY' ORDER BY project_slug")
        print(f"  csnl_research.projects (init='MSY'): {len(rows or [])} project(s)")
        seeded = {a["aim_label"] for a in COLD_START_AIMS}
        live_slugs = set()
        for r in rows or []:
            slug = r.get("project_slug")
            live_slugs.add(slug)
            flag = "seeded" if slug in seeded else "NOT seeded"
            print(f"    - {slug} [{r.get('phase')}] ({flag})")
            print(f"        title: {(r.get('title') or '')[:88]}")
            if r.get("hypothesis"):
                print(f"        hyp:   {(r.get('hypothesis') or '')[:110]}")
        missing_live = seeded - live_slugs
        if missing_live:
            print(f"  ⚠ seed references slugs absent from live projects: "
                  f"{sorted(missing_live)} — review before --apply.")
        extra_live = live_slugs - seeded
        if extra_live:
            print(f"  note: live projects not seeded (fine if intentional): "
                  f"{sorted(extra_live)}")

        print("\n  existing archive_survey_* rows for MSY:")
        for t in ("archive_survey_aims", "archive_survey_keywords",
                  "archive_survey_methods", "archive_survey_profile",
                  "archive_survey_negatives"):
            c = query_json(f"SELECT count(*) n FROM {sch}.{t} WHERE researcher_id='MSY'")
            print(f"    {t}: {(c or [{}])[0].get('n')}")
        print("  (--apply would DELETE+INSERT aims/keywords/methods for MSY only; "
              "profile/negatives untouched.)")
        return 0
    except Exception as e:  # pragma: no cover
        print(f"[check-live] read-only query failed ({e}); non-fatal.",
              file=sys.stderr)
        return 0


# ===========================================================================
# --apply : MSY-only, three-table transactional write (operator-gated)
# ===========================================================================

def apply() -> int:
    from _db import load_env, ledger_schema, _conn  # noqa: E402
    load_env()
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        raise SystemExit("cold_start_msy --apply requires psycopg2-binary.")
    sch = ledger_schema()
    seed = _structured_seed()

    # hard invariant guards — this path may ONLY touch MSY + the 3 tables.
    for grp in ("aims", "keywords", "methods"):
        for row in seed[grp]:
            assert row["researcher_id"] == RID, "non-MSY row in cold-start seed"
    issues = validate()
    if issues:
        raise SystemExit(f"cold_start_msy --apply refused: validation issues {issues}")

    conn = _conn()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            for tbl in TARGET_TABLES:
                cur.execute(f"DELETE FROM {sch}.{tbl} WHERE researcher_id=%s", (RID,))

            for a in seed["aims"]:
                cur.execute(f"""
                    INSERT INTO {sch}.archive_survey_aims
                      (researcher_id,aim_id,aim_label,hyp_type,domain,phenomenon,
                       task,mechanism,population,metric,condition,direction,
                       hypothesis,background,seed_paper,measures,confidence,
                       source_version,raw_jsonb)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s::jsonb,%s,%s,%s::jsonb)
                """, (a["researcher_id"], a["aim_id"], a["aim_label"], a["hyp_type"],
                      a["domain"], a["phenomenon"], a["task"], a["mechanism"],
                      a["population"], a["metric"], a["condition"], a["direction"],
                      a["hypothesis"], a["background"], a["seed_paper"],
                      json.dumps(a["measures"], ensure_ascii=False),
                      a["confidence"], a["source_version"],
                      json.dumps(a["raw_jsonb"], ensure_ascii=False)))

            for k in seed["keywords"]:
                cur.execute(f"""
                    INSERT INTO {sch}.archive_survey_keywords
                      (researcher_id,keyword,is_ambiguous,operational_def,
                       conflict_term,bound_aim,source_version,raw_jsonb)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """, (k["researcher_id"], k["keyword"], k["is_ambiguous"],
                      k["operational_def"], k["conflict_term"], k["bound_aim"],
                      k["source_version"],
                      json.dumps(k["raw_jsonb"], ensure_ascii=False)))

            for m in seed["methods"]:
                cur.execute(f"""
                    INSERT INTO {sch}.archive_survey_methods
                      (researcher_id,modality,approach,raw_label,source_version)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (researcher_id,modality,approach) DO NOTHING
                """, (m["researcher_id"], m["modality"], m["approach"],
                      m["raw_label"], m["source_version"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"\n[cold-start] UPSERT complete for {RID}: "
          f"{len(seed['aims'])} aims, {len(seed['keywords'])} keywords, "
          f"{len(seed['methods'])} methods (confidence={CONFIDENCE}, "
          f"source_version={SOURCE_VERSION}).")
    print("[cold-start] MSY now runs the P28 connection engine instead of legacy "
          "fallback. Re-run recommend.py --only MSY --profile-source db to verify.")
    return 0


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="Dump the structured seed as JSON (offline).")
    ap.add_argument("--check-live", action="store_true",
                    help="READ-ONLY cross-check vs csnl_research.projects + existing rows.")
    ap.add_argument("--apply", action="store_true",
                    help="MSY-only write to archive_survey_{aims,keywords,methods} "
                         "(operator-gated).")
    args = ap.parse_args()

    if args.json:
        print(json.dumps(_structured_seed(), ensure_ascii=False, indent=2))
    else:
        _print_seed()

    if args.check_live:
        check_live()

    if args.apply:
        return apply()

    print("\n[cold-start] dry-run only. Re-run with --apply (operator) to write "
          "archive_survey_* for MSY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
