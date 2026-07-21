#!/usr/bin/env python3
"""
scripts/archive/_tests/test_p33.py — P33 offline acceptance suite (U8, MF-9/MF-13).

PURE-FUNCTION, OFFLINE ONLY. This suite NEVER opens a network socket, NEVER
connects to prod, and NEVER shells out to a `_db`-touching script. It imports the
REAL P33 functions as implemented and drives them over synthetic fixtures, plus a
static lint over the migration `.sql`. Everything that would touch prod
(`build_digest`/`recommend`/`fetch_new_papers --apply`/`ingest_live_papers`) is
operator-run and is deliberately NOT exercised here.

Coverage (the REVISED Acceptance fixtures):
  (i)   same-work dedup           — `_common.same_work` / `_common.dedup_same_work`
        drift collapse / same-title-different-DOI kept / blank-title keeps both /
        dedup keeps the max-composite representative.
  (ii)  build_digest exclusion    — `build_digest._drop_same_work` (pure): a
        read/answered preprint suppresses its published twin; a distinct paper
        with a different DOI is kept.
  (iii) watermark tri-state       — `fetch_new_papers.compute_watermark_advance` +
        `compute_fetch_window_start`: late-index still fetched (overlap window),
        GREATEST advance never regresses, a 429/timeout (failed) run and an
        ok+empty run both leave the cursor unmoved.
  (iv)  S2 body builder           — `fetch_new_papers.to_s2_id` /
        `build_s2_recommendation_body` / `egress_is_clean`: DOI:/ARXIV: mapping,
        no-DOI skip, dedup+cap, and the egress rule (body carries ONLY public
        paper IDs — no researcher identity / no `.env` value).
  (v)   migration lint            — `state/migrations/2026-07-20_p33_pipeline.sql`
        is csnl_paper_rec.-qualified, single BEGIN/COMMIT, no DROP / SET
        search_path / csnl_research, and the title_norm index is NON-UNIQUE.

Run:
    python3 -m pytest scripts/archive/_tests/test_p33.py -q
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest

# --- offline path wiring (repo modules; no DB connection at import) ----------
ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "pipeline", ROOT / "scripts" / "archive", ROOT / "scripts" / "weekly"):
    sp = str(_p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import _common as C                     # noqa: E402  (same_work / dedup_same_work / norm_title)
import fetch_new_papers as F            # noqa: E402  (watermark + S2 pure functions)
import build_digest as BD               # noqa: E402  (_drop_same_work pure exclusion)

MIGRATION = ROOT / "state" / "migrations" / "2026-07-20_p33_pipeline.sql"

# rapidfuzz backs the fuzzy-title branch of same_work(); when it is absent that
# branch conservatively refuses to merge, so the drift-collapse fixtures cannot
# be exercised. Skip (don't fail) those, exactly matching production degradation.
_HAS_RAPIDFUZZ = getattr(C, "_token_set_ratio", None) is not None
_needs_rapidfuzz = pytest.mark.skipif(
    not _HAS_RAPIDFUZZ, reason="rapidfuzz absent -> same_work title branch is a no-merge no-op")


# --- shared synthetic titles -------------------------------------------------
# A realistic preprint<->published DRIFT: one word changes ('in' -> 'of'). After
# norm_title (which folds to a spaceless alnum string) the pair scores
# token_set_ratio ~= 94 >= the default fuzz=92 threshold, so it collapses.
_PREPRINT_TITLE = "Serial dependence in visual perception"
_PUBLISHED_TITLE = "Serial dependence of visual perception"
_DISTINCT_TITLE = "Bayesian models of scene perception"

_TN_PRE = C.norm_title(_PREPRINT_TITLE)
_TN_PUB = C.norm_title(_PUBLISHED_TITLE)
_TN_OTHER = C.norm_title(_DISTINCT_TITLE)


# =========================================================================
# (i) same-work dedup  — _common.same_work / _common.dedup_same_work
# =========================================================================

@_needs_rapidfuzz
def test_same_work_drift_collapses_synthetic_vs_real():
    """A preprint (synthetic arxiv DOI) + its published twin (real DOI) with a
    DRIFTED title collapse via the title branch (one side is DOI-less)."""
    pre = {"doi": "arxiv:2401.09999", "title_norm": _TN_PRE}
    pub = {"doi": "10.1038/s41593-024-0100", "title_norm": _TN_PUB}
    assert C.same_work(pre, pub) is True
    assert C.same_work(pub, pre) is True  # symmetric


def test_same_work_two_real_different_dois_never_merge_even_same_title():
    """Two REAL but DIFFERENT DOIs are authoritative: identical title_norm must
    NOT title-merge them (guards against dropping legit distinct papers)."""
    a = {"doi": "10.1/aaa", "title_norm": "sametitlehere"}
    b = {"doi": "10.2/bbb", "title_norm": "sametitlehere"}
    assert C.same_work(a, b) is False


def test_same_work_equal_real_doi_merges():
    a = {"doi": "https://doi.org/10.1038/ABC", "title_norm": "one title"}
    b = {"doi": "10.1038/abc", "title_norm": "a totally different string"}
    # Equal real DOI after norm_doi -> same work regardless of title.
    assert C.same_work(a, b) is True


def test_same_work_blank_title_keeps_both_when_doi_missing():
    """Blank title_norm on the DOI-missing side is never a merge key."""
    a = {"doi": None, "title_norm": ""}
    b = {"doi": None, "title_norm": ""}
    assert C.same_work(a, b) is False
    # synthetic DOI + blank title also refuses to merge
    c = {"doi": "arxiv:2401.1", "title_norm": ""}
    d = {"doi": "arxiv:2401.2", "title_norm": "some real title"}
    assert C.same_work(c, d) is False


@_needs_rapidfuzz
def test_dedup_same_work_keeps_max_composite_representative():
    """The drift twins collapse to ONE row (the higher composite survives); the
    genuinely distinct paper is kept."""
    pre = {"id": "pre", "doi": "arxiv:2401.09999", "title_norm": _TN_PRE, "composite": 0.50}
    pub = {"id": "pub", "doi": "10.1038/s41593-024-0100", "title_norm": _TN_PUB, "composite": 0.90}
    other = {"id": "other", "doi": "10.2/other", "title_norm": _TN_OTHER, "composite": 0.70}
    kept = C.dedup_same_work([pre, pub, other])
    ids = {r["id"] for r in kept}
    assert len(kept) == 2
    assert ids == {"pub", "other"}          # max-composite twin ('pub') survives
    assert "pre" not in ids


def test_dedup_same_work_same_title_diff_real_doi_both_kept():
    """Two real distinct DOIs with an identical title are BOTH kept."""
    a = {"id": "a", "doi": "10.1/aaa", "title_norm": "sametitlehere", "composite": 0.3}
    b = {"id": "b", "doi": "10.2/bbb", "title_norm": "sametitlehere", "composite": 0.8}
    kept = C.dedup_same_work([a, b])
    assert {r["id"] for r in kept} == {"a", "b"}


def test_dedup_same_work_blank_title_no_reliable_key_keeps_all():
    """No real DOI AND blank title_norm -> no reliable key -> all kept."""
    x = {"id": "x", "doi": None, "title_norm": "", "composite": 0.1}
    y = {"id": "y", "doi": None, "title_norm": "", "composite": 0.2}
    kept = C.dedup_same_work([x, y])
    assert {r["id"] for r in kept} == {"x", "y"}


def test_dedup_same_work_stable_order_and_singleton_passthrough():
    assert C.dedup_same_work([]) == []
    solo = [{"id": "only", "doi": "10.1/z", "title_norm": "t", "composite": 1.0}]
    assert C.dedup_same_work(solo) == solo


# =========================================================================
# (ii) build_digest same-work exclusion  — build_digest._drop_same_work
# =========================================================================

@_needs_rapidfuzz
def test_build_digest_drops_published_twin_of_answered_preprint():
    """The researcher ANSWERED a preprint (synthetic DOI); its published twin
    (different canonical_id + real DOI) must be excluded from the candidates,
    while a distinct paper is retained (MF-2 twin-leak closure)."""
    known = [{"doi": "arxiv:2401.09999", "title_norm": _TN_PRE}]  # answered preprint
    twin = {"canonical_id": "c_twin", "doi": "10.1038/s41593-024-0100", "title_norm": _TN_PUB}
    distinct = {"canonical_id": "c_keep", "doi": "10.2/other", "title_norm": _TN_OTHER}
    kept = BD._drop_same_work([twin, distinct], known)
    ids = [c["canonical_id"] for c in kept]
    assert ids == ["c_keep"]                 # twin dropped, distinct kept, order preserved


def test_build_digest_drop_same_work_empty_known_is_passthrough():
    cands = [{"canonical_id": "c1", "doi": "10.1/a", "title_norm": "t1"},
             {"canonical_id": "c2", "doi": "10.2/b", "title_norm": "t2"}]
    kept = BD._drop_same_work(cands, [])
    assert [c["canonical_id"] for c in kept] == ["c1", "c2"]


def test_build_digest_distinct_real_doi_not_dropped_by_shared_title():
    """A candidate that only shares a title with a KNOWN real-DOI paper (both
    real, different DOIs) is NOT dropped — same_work refuses to title-merge two
    real DOIs, so distinct works survive."""
    known = [{"doi": "10.1/known", "title_norm": "sametitlehere"}]
    cand = {"canonical_id": "c1", "doi": "10.2/cand", "title_norm": "sametitlehere"}
    kept = BD._drop_same_work([cand], known)
    assert [c["canonical_id"] for c in kept] == ["c1"]


# =========================================================================
# (iii) watermark tri-state + advance  — fetch_new_papers
# =========================================================================

def test_watermark_advances_to_greatest_on_success():
    stored = date(2026, 7, 1)
    new = F.compute_watermark_advance(stored, [date(2026, 7, 5), date(2026, 6, 20)], failed=False)
    assert new == date(2026, 7, 5)          # GREATEST(stored, batch-max)


def test_watermark_never_regresses_below_stored():
    stored = date(2026, 7, 1)
    new = F.compute_watermark_advance(stored, [date(2026, 6, 20)], failed=False)
    assert new == stored                    # older batch never pulls the cursor back


def test_watermark_failed_run_leaves_cursor_unmoved():
    """A 429 / timeout / exception (failed=True) must NOT advance even with a
    newer batch — the whole point of the tri-state rail."""
    stored = date(2026, 7, 1)
    new = F.compute_watermark_advance(stored, [date(2026, 7, 10)], failed=True)
    assert new == stored
    # failed cold-start likewise stays None
    assert F.compute_watermark_advance(None, [date(2026, 7, 10)], failed=True) is None


def test_watermark_ok_empty_leaves_cursor_unmoved():
    stored = date(2026, 7, 1)
    assert F.compute_watermark_advance(stored, [], failed=False) == stored
    assert F.compute_watermark_advance(stored, [None, None], failed=False) == stored


def test_watermark_cold_start_and_none_filtering():
    assert F.compute_watermark_advance(None, [date(2026, 7, 5)], failed=False) == date(2026, 7, 5)
    # None entries in the batch are ignored, not treated as a min.
    new = F.compute_watermark_advance(date(2026, 7, 1), [None, date(2026, 7, 9), None], failed=False)
    assert new == date(2026, 7, 9)


def test_fetch_window_overlap_still_covers_late_indexed_paper():
    """The lookback-overlap window re-includes a paper indexed a few days BEFORE
    the stored cursor (it surfaced late) so it is still fetched; dedup then drops
    anything already ingested."""
    stored = date(2026, 7, 1)
    start = F.compute_fetch_window_start(stored, lookback_days=7)
    assert start == date(2026, 6, 24)
    late_indexed = date(2026, 6, 28)        # indexed after last run, older index date
    assert start <= late_indexed            # -> inside the fetch window


def test_fetch_window_cold_start_and_future_clamp():
    cold = F.compute_fetch_window_start(None, default_lookback_days=30, today=date(2026, 7, 20))
    assert cold == date(2026, 6, 20)
    # a cursor in the future is clamped to today (never fetch from the future).
    clamped = F.compute_fetch_window_start(date(2027, 1, 1), lookback_days=7, today=date(2026, 7, 20))
    assert clamped == date(2026, 7, 20)


# =========================================================================
# (iv) S2 body builder  — mapping / no-DOI skip / no identity+env leakage
# =========================================================================

def test_to_s2_id_mapping():
    assert F.to_s2_id("10.1234/abc") == "DOI:10.1234/abc"
    assert F.to_s2_id("https://doi.org/10.1234/ABC") == "DOI:10.1234/abc"   # normalised+lowercased
    assert F.to_s2_id("arxiv:2401.00001") == "ARXIV:2401.00001"
    assert F.to_s2_id(None) is None
    assert F.to_s2_id("") is None


def test_build_s2_body_maps_dedups_caps_and_skips_no_doi():
    pos = ["10.1/a", "10.1/a", "arxiv:2401.1", None, "", "10.1/b"]
    neg = ["10.2/x", "10.2/x"]
    body = F.build_s2_recommendation_body(pos, neg)
    assert body["positivePaperIds"] == ["DOI:10.1/a", "ARXIV:2401.1", "DOI:10.1/b"]
    assert body["negativePaperIds"] == ["DOI:10.2/x"]
    # cap is honoured
    capped = F.build_s2_recommendation_body([f"10.9/{i}" for i in range(10)], max_pos=3)
    assert len(capped["positivePaperIds"]) == 3


def test_build_s2_body_omits_negative_key_when_empty():
    body = F.build_s2_recommendation_body(["10.1/a"], None)
    assert set(body.keys()) == {"positivePaperIds"}


def test_s2_body_egress_is_clean_for_legit_body():
    body = F.build_s2_recommendation_body(["10.1/a", "arxiv:2401.1"], ["10.2/x"])
    assert F.egress_is_clean(body) is True
    assert set(body.keys()) <= {"positivePaperIds", "negativePaperIds"}


def test_s2_egress_rejects_researcher_identity_and_env_leak():
    """The egress rule: any extra key (researcher_id / mailto / env) OR any
    email-shaped value ('@') fails the check -> the caller refuses to POST."""
    leaked_key = {"positivePaperIds": ["DOI:10.1/a"], "researcher_id": "BHL"}
    assert F.egress_is_clean(leaked_key) is False
    leaked_mailto = {"positivePaperIds": ["DOI:10.1/a"], "mailto": "lab@example.org"}
    assert F.egress_is_clean(leaked_mailto) is False
    leaked_value = {"positivePaperIds": ["DOI:10.1/a", "lab@example.org"]}
    assert F.egress_is_clean(leaked_value) is False
    assert F.egress_is_clean("not-a-dict") is False


def test_s2_built_body_never_contains_identity_or_env():
    """End-to-end over a realistic seed list: whatever is built is egress-clean
    and its values are only DOI:/ARXIV: ID strings (no name/email/env)."""
    body = F.build_s2_recommendation_body(
        ["10.1/save1", "arxiv:2401.5", "10.1/read2"], ["10.2/notrel"])
    assert F.egress_is_clean(body)
    for v in body["positivePaperIds"] + body.get("negativePaperIds", []):
        assert v.startswith(("DOI:", "ARXIV:", "CorpusId:"))
        assert "@" not in v


def test_s2_egress_strict_allowlist_rejects_non_id_values():
    """Guardrail hardening: the egress check is a STRICT public-ID allowlist, not
    merely an '@'/extra-key filter. A bare researcher initial, a Korean name, or
    an env-looking secret must FAIL even though none of them contains '@'."""
    for bad in ("BHL", "박준오", "ntn_secret_token_value", "10.1/a", "DOI:", "", "   "):
        assert F.egress_is_clean({"positivePaperIds": ["DOI:10.1/ok", bad]}) is False, bad


def test_s2_egress_rejects_bare_string_value_not_char_iterated():
    """A raw-string body used to be char-iterated ('B','H','L' each look clean)
    and PASSED. Each key's value must be a list."""
    assert F.egress_is_clean({"positivePaperIds": "BHL"}) is False
    assert F.egress_is_clean(
        {"positivePaperIds": ["DOI:10.1/ok"], "negativePaperIds": "BHL"}) is False


def test_s2_egress_rejects_oversized_and_accepts_the_three_legit_shapes():
    assert F.egress_is_clean({"positivePaperIds": ["DOI:10.1/" + "x" * 300]}) is False
    assert F.egress_is_clean({"positivePaperIds": [
        "DOI:10.1038/s41593-024-1", "ARXIV:2401.12345", "CorpusId:123456"]}) is True


# =========================================================================
# (v) migration lint  — static, string-aware; NO DB
# =========================================================================

def _split_statements(sql: str) -> list[str]:
    """Comment-stripped, string-aware statement split. Removes `--` line comments
    when not inside a single-quoted literal, honours '' escapes, and splits on a
    `;` only outside a string (so a ';' embedded in a COMMENT literal does not
    fracture the statement)."""
    stmts, buf = [], []
    i, n, in_str = 0, len(sql), False
    while i < n:
        ch = sql[i]
        if in_str:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":   # '' escaped quote
                    buf.append("''"); i += 2; continue
                in_str = False
            buf.append(ch); i += 1; continue
        if ch == "'":
            in_str = True; buf.append(ch); i += 1; continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":   # line comment
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if ch == ";":
            stmts.append("".join(buf).strip()); buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return [s for s in stmts if s]


def test_migration_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_single_begin_single_commit():
    stmts = _split_statements(MIGRATION.read_text(encoding="utf-8"))
    begins = [s for s in stmts if re.match(r"(?i)begin\b", s)]
    commits = [s for s in stmts if re.match(r"(?i)commit\b", s)]
    assert len(begins) == 1, f"expected exactly one BEGIN, got {len(begins)}"
    assert len(commits) == 1, f"expected exactly one COMMIT, got {len(commits)}"


def test_migration_every_ddl_dml_is_schema_qualified():
    stmts = _split_statements(MIGRATION.read_text(encoding="utf-8"))
    ddl = [s for s in stmts if re.match(r"(?i)^(create|alter|update|insert|comment)\b", s)]
    assert ddl, "no DDL/DML statements found — parser or file is wrong"
    for s in ddl:
        assert "csnl_paper_rec." in s, f"statement not csnl_paper_rec.-qualified:\n{s[:120]}"


def test_migration_has_no_drop_searchpath_or_research_schema():
    body = "\n".join(_split_statements(MIGRATION.read_text(encoding="utf-8")))
    assert not re.search(r"(?i)\bdrop\b", body), "DROP is forbidden in this migration"
    assert not re.search(r"(?i)set\s+search_path", body), "SET search_path is forbidden"
    assert "csnl_research" not in body, "must not reference the read-only research schema"


def test_migration_title_norm_index_is_non_unique():
    stmts = _split_statements(MIGRATION.read_text(encoding="utf-8"))
    idx = [s for s in stmts if re.match(r"(?i)^create\s+(unique\s+)?index", s)]
    assert idx, "no CREATE INDEX found"
    for s in idx:
        assert not re.search(r"(?i)\bunique\b", s), f"index must be NON-UNIQUE:\n{s[:120]}"
        assert "csnl_paper_rec." in s, "index ON-clause must be schema-qualified"


def test_migration_declares_the_three_expected_objects():
    stmts = _split_statements(MIGRATION.read_text(encoding="utf-8"))
    joined = "\n".join(stmts)
    assert any(re.match(r"(?i)^create\s+table", s) and "archive_discovery_watermark" in s
               for s in stmts), "watermark table missing"
    assert any(re.match(r"(?i)^alter\s+table", s) and "builder" in s for s in stmts), \
        "builder column ALTER missing"
    assert "idx_archive_papers_title_norm" in joined, "title_norm index missing"
    # additive guards present (idempotent re-application)
    assert re.search(r"(?i)create\s+table\s+if\s+not\s+exists", joined)
    assert re.search(r"(?i)add\s+column\s+if\s+not\s+exists", joined)
    assert re.search(r"(?i)create\s+index\s+if\s+not\s+exists", joined)


def test_migration_backfills_builder_to_brq():
    """MF-D: a static column DEFAULT alone leaves pre-existing rows builder=NULL,
    and build_digest's `q.builder='brq'` filter would then return ZERO candidates
    (empty digests for everyone). Lock the idempotent backfill UPDATE so deleting
    it fails CI rather than silently emptying the digest pool."""
    body = "\n".join(_split_statements(MIGRATION.read_text(encoding="utf-8")))
    assert re.search(
        r"(?is)update\s+csnl_paper_rec\.archive_researcher_queues\s+set\s+"
        r"builder\s*=\s*'brq'\s+where\s+builder\s+is\s+null",
        body,
    ), "missing idempotent builder='brq' backfill for pre-existing NULL rows"


# =========================================================================
# (vi) canonical_id PK stability — MF-C regression guard (pure; NO DB)
# =========================================================================

def test_canonical_id_byte_stable_for_common_doi_forms():
    """canonical_id feeds the archive_papers PRIMARY KEY. norm_doi must be
    byte-stable for the common DOI forms (MF-C): a drift silently orphans every
    stored row (and its canonical_id-keyed archive_responses) on re-ingest.
    The bare / doi.org / dx.doi.org URL forms MUST collapse to one id."""
    bare = C.canonical_id("10.1038/s41593-024-0100", None, None)
    url = C.canonical_id("https://doi.org/10.1038/s41593-024-0100", None, None)
    dxurl = C.canonical_id("http://dx.doi.org/10.1038/s41593-024-0100", None, None)
    assert bare == url == dxurl, "URL DOI forms must normalize to one canonical_id"


def test_norm_doi_does_not_strip_doi_prefix_pk_stability():
    """MF-C: norm_doi (which feeds the PK) must NOT strip a leading 'doi:' — that
    would shift the PK for 'doi:'-prefixed rows. The strip is comparison-only."""
    assert C.norm_doi("doi:10.1038/X") == "doi:10.1038/x"        # prefix PRESERVED
    assert C.norm_doi("10.1038/X") == "10.1038/x"
    # a 'doi:'-prefixed input therefore hashes to a DIFFERENT PK than the bare
    # form — the historical (pre-P33) behaviour, locked so a future edit fails CI.
    assert C.canonical_id("doi:10.1/a", None, None) != C.canonical_id("10.1/a", None, None)


def test_same_work_collapses_doi_prefixed_vs_bare_via_cmp_doi():
    """MF-C: while norm_doi preserves 'doi:' for PK stability, the comparison
    layer (_cmp_doi) strips it, so same_work still collapses the two string forms
    of one real DOI."""
    assert C.same_work({"doi": "doi:10.1/a", "title_norm": ""},
                       {"doi": "10.1/a", "title_norm": ""})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
