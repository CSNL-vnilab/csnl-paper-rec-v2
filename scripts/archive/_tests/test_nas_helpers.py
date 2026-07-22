#!/usr/bin/env python3
"""
scripts/archive/_tests/test_nas_helpers.py — P34 offline suite for the two
share-wide NAS safety helpers in `_common`: `nfc()` and `safe_walk()`.

OFFLINE BY DEFAULT. Every test below runs against a synthetic tmp tree that
reproduces the exact traps measured on the live share
(state/archive/_explore/batch02/N6-map.md): a self-referential symlink, a
recycle-bin shadow copy, an EACCES subtree, and NFD filenames. No network, no
DB, no NAS access unless the opt-in live check is enabled.

  offline:  python3 -m pytest scripts/archive/_tests/test_nas_helpers.py -q
  + live:   CSNL_NAS_LIVE=1 python3 -m pytest scripts/archive/_tests/test_nas_helpers.py -q

The live check is opt-in on purpose: the NAS auto-bans repeated logins and
tolerates only a handful of concurrent readers, so a bounded read-only walk
must never run implicitly on every test invocation.

Evidence anchors:
  N6-2  root self-symlink 'CSNL_new-1' -> /Volumes/CSNL_new-1 (infinite loop),
        '#recycle' / '@Recycle' shadow copies (double-count),
        'Temp_188_BRL' EACCES (aborts an unguarded walk).
  N6-3  Memory/Papers filenames are NFD: name-regex conformance
        4775/4868 = 98.09% raw -> 4865/4868 = 99.94% after NFC.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

import pytest

# --- offline path wiring (repo modules; no DB connection at import) ----------
ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "pipeline", ROOT / "scripts" / "archive"):
    sp = str(_p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import _common as C                      # noqa: E402

CATALOG_PATH = ROOT / "config" / "nas_catalog.json"

# The catalog's measured name regex (unicode-aware) — used verbatim so the NFC
# assertion below is the SAME check the ingest path makes.
PAPER_RE = re.compile(
    r"^([^\W\d_][^\W\d]*(?:[-'’][^\W\d]+)*)_((?:1[89]|20)\d{2})_(.+)\.pdf$",
    re.UNICODE,
)

# Real filenames observed on the share (catalog `unicode.examples_nfd`) — these
# are stored NFD, which is precisely why the raw regex misses them.
REAL_NFD_STEMS = [
    "Ásgeirsson_2017_Retro_cue_benefits.pdf",       # Ásgeirsson
    "ÅKERSTEDT_2005_Sleep_and_alertness.pdf",       # ÅKERSTEDT
    "Barabási_2012_The_network_takeover_v2.pdf",    # Barabási
    "Başar_2013_Brain_oscillations.pdf",            # Başar
    "deCheveigné_2008_Time_domain_filtering.pdf",   # deCheveigné
    "Darquié_2011_Feedforward_and_feedback.pdf",    # Darquié
]

MANDATED_PRUNE = {
    "CSNL_new-1", "._CSNL_new-1", "#recycle", "@Recycle",
    ".Trashes", ".TemporaryItems",
}


# =========================================================================
# (i) nfc() — the NFD silent-miss class (N6-3)
# =========================================================================

def test_nfc_folds_real_nfd_surname_to_its_nfc_form():
    """The measured failure mode: two strings that RENDER identically compare
    unequal, so a catalog/DB lookup silently misses the file."""
    nfd = unicodedata.normalize("NFD", "Ásgeirsson")   # 'A' + U+0301
    nfc_form = unicodedata.normalize("NFC", "Ásgeirsson")
    assert nfd != nfc_form                    # raw comparison FAILS ...
    assert len(nfd) == len(nfc_form) + 1      # ... because NFD is longer
    assert C.nfc(nfd) == C.nfc(nfc_form)      # ... and nfc() repairs it
    assert C.nfc_eq(nfd, nfc_form)


@pytest.mark.parametrize("name", REAL_NFD_STEMS)
def test_nfc_restores_regex_conformance_on_real_filenames(name):
    """N6-3 root cause: `\\w` / `[^\\W\\d_]` exclude combining marks (Mn), so an
    NFD diacritic surname fails the author-year-title regex. NFC fixes it.
    This is the 98.09% -> 99.94% conformance jump, asserted per filename."""
    as_nfd = unicodedata.normalize("NFD", name)
    assert PAPER_RE.match(as_nfd) is None            # the silent miss
    m = PAPER_RE.match(C.nfc(as_nfd))                # the fix
    assert m is not None
    assert 1800 <= int(m.group(2)) <= 2099


def test_nfc_regex_conformance_delta_over_the_real_sample():
    """Same claim stated as a rate, mirroring the catalog's measurement."""
    names = [unicodedata.normalize("NFD", n) for n in REAL_NFD_STEMS]
    raw_ok = sum(1 for n in names if PAPER_RE.match(n))
    nfc_ok = sum(1 for n in names if PAPER_RE.match(C.nfc(n)))
    assert raw_ok == 0
    assert nfc_ok == len(names)


def test_nfc_accepts_none_path_and_is_idempotent():
    assert C.nfc(None) == ""
    assert C.nfc("") == ""
    assert C.nfc(Path("/tmp/é")) == C.nfc("/tmp/" + unicodedata.normalize("NFD", "é"))
    once = C.nfc(unicodedata.normalize("NFD", "Brückner"))
    assert C.nfc(once) == once                       # idempotent
    assert C.nfc("Smith_2011_Plain_ascii.pdf") == "Smith_2011_Plain_ascii.pdf"


def test_nfc_handles_korean_names_from_the_catalog():
    """Korean NAS filenames are NFD too (jamo-decomposed); the catalog's own
    'PACS설치 파일' entry is the documented live demonstration."""
    korean = "PACS설치 파일"
    decomposed = unicodedata.normalize("NFD", korean)
    assert decomposed != korean
    assert C.nfc_eq(decomposed, korean)


def test_nfc_does_not_touch_pk_stable_helpers():
    """Guard-rail: the P34 additions must not have perturbed the PK family."""
    assert C.canonical_id("10.1038/x", None, None) == C.canonical_id("10.1038/X", None, None)
    assert C.norm_doi("doi:10.1/a") == "doi:10.1/a"      # 'doi:' still preserved
    assert C.canonical_id(None, "Serial dependence", 2020) == \
        C.canonical_id(None, "serial  dependence!", 2020)


# =========================================================================
# (ii) catalog-driven prune list — data edits widen, never narrow
# =========================================================================

def test_prune_set_contains_every_mandated_trap():
    names = C.nas_prune_names()
    for n in MANDATED_PRUNE:
        assert C.nfc(n) in names, n


def test_prune_set_unions_the_live_catalog():
    """A trap recorded in config/nas_catalog.json must be honoured by code that
    never mentions it — the operator directive: convention change = DATA edit."""
    cat = C.load_nas_catalog(CATALOG_PATH, refresh=True)
    assert cat, "config/nas_catalog.json missing or unreadable"
    recorded = {C.nfc(r["name"]) for r in cat["walk_rules"]["never_descend"]}
    assert recorded, "catalog records no never_descend entries"
    assert recorded <= C.nas_prune_names(cat)
    assert C.nfc("Temp_188_BRL") in C.nas_prune_names(cat)      # EACCES subtree
    deep = {C.nfc(n) for n in cat["walk_rules"]["never_walk_deep"]["names"]}
    assert deep <= C.nas_shallow_names(cat)


def test_catalog_edit_cannot_delete_a_verified_trap():
    """Union direction: an emptied / malformed catalog degrades to the built-in
    floor rather than to an unguarded walk."""
    for bogus in ({}, {"walk_rules": {}}, {"walk_rules": {"never_descend": []}},
                  {"walk_rules": {"never_descend": "CSNL_new-1"}}, None):
        names = C.nas_prune_names(bogus if bogus is not None else {})
        assert C.nfc("CSNL_new-1") in names
        assert C.nfc("#recycle") in names


def test_missing_catalog_file_returns_empty_dict_not_raise(tmp_path):
    assert C.load_nas_catalog(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert C.load_nas_catalog(bad) == {}
    assert C.nfc("CSNL_new-1") in C.nas_prune_names(C.load_nas_catalog(bad))


def test_catalog_extra_name_is_picked_up_without_a_code_change():
    cat = {"walk_rules": {"never_descend": [{"name": "NewTrapDir_2027", "why": "x"}],
                          "never_walk_deep": {"names": ["HugeRawDir"]}}}
    assert C.nfc("NewTrapDir_2027") in C.nas_prune_names(cat)
    assert C.nfc("HugeRawDir") in C.nas_shallow_names(cat)


# =========================================================================
# (iii) safe_walk() — the walker traps (N6-2)
# =========================================================================

def _mk_tree(tmp_path: Path) -> Path:
    """Reproduce the share-root traps in miniature."""
    root = tmp_path / "share"
    (root / "Memory" / "Papers").mkdir(parents=True)
    (root / "Memory" / "Papers" / "Smith_2011_Real.pdf").write_text("x", encoding="utf-8")
    (root / "GRM" / "2026").mkdir(parents=True)
    (root / "GRM" / "2026" / "PB_260715.pptx").write_text("x", encoding="utf-8")
    # recycle-bin shadow copy (would double-count every file)
    (root / "#recycle" / "Memory" / "Papers").mkdir(parents=True)
    (root / "#recycle" / "Memory" / "Papers" / "Smith_2011_Real.pdf").write_text("x", encoding="utf-8")
    (root / "@Recycle").mkdir()
    (root / "@Recycle" / "zz.nii").write_text("x", encoding="utf-8")
    # the self-referential symlink, by its real name ...
    os.symlink(str(root), str(root / "CSNL_new-1"))
    # ... and an UNNAMED loop symlink: the structural guard, not the name prune
    os.symlink(str(root), str(root / "loop_link"))
    # a symlink to a regular file — legitimate, must survive as a file
    os.symlink(str(root / "GRM" / "2026" / "PB_260715.pptx"), str(root / "shortcut.pptx"))
    return root


def _walk(root, **kw):
    st = C.new_walk_stats()
    out = list(C.safe_walk(root, stats=st, **kw))
    return out, st


def test_safe_walk_terminates_and_never_follows_the_self_symlink(tmp_path):
    """N6-2: the headline failure. An os.walk(followlinks=True) here never
    returns; safe_walk must finish and visit each real directory exactly once."""
    root = _mk_tree(tmp_path)
    walked, st = _walk(root)
    dirs = [d for d, _, _ in walked]
    assert len(dirs) == len(set(dirs)), "a directory was visited twice"
    assert not any("CSNL_new-1" in d for d in dirs)
    assert not any("loop_link" in d for d in dirs)
    # ... and neither symlink is even reported as a directory to the caller
    for _, dirnames, _ in walked:
        assert "CSNL_new-1" not in dirnames
        assert "loop_link" not in dirnames
    assert st["symlinks_skipped_n"] >= 2
    # the real tree IS walked
    assert str(root / "Memory" / "Papers") in dirs
    assert str(root / "GRM" / "2026") in dirs


def test_safe_walk_symlink_guard_is_structural_not_name_based(tmp_path):
    """'loop_link' is in no prune list; it is skipped because it is a symlink.
    This is what protects us from the NEXT unnamed loop."""
    root = _mk_tree(tmp_path)
    walked, st = _walk(root, prune=())          # prune set fully disabled
    dirs = [d for d, _, _ in walked]
    assert not any("loop_link" in d for d in dirs)
    assert not any(d.endswith("CSNL_new-1") for d in dirs)
    assert len(dirs) == len(set(dirs))
    assert any("#recycle" in d for d in dirs), "prune=() should re-admit the bin"


def test_safe_walk_keeps_a_symlink_to_a_regular_file(tmp_path):
    root = _mk_tree(tmp_path)
    walked, _ = _walk(root)
    top = [f for d, _, f in walked if d == str(root)][0]
    assert "shortcut.pptx" in top               # reading it cannot recurse


def test_safe_walk_prunes_both_recycle_shadow_copies(tmp_path):
    root = _mk_tree(tmp_path)
    walked, st = _walk(root)
    files = [os.path.join(d, f) for d, _, fs in walked for f in fs]
    assert sum(1 for f in files if f.endswith("Smith_2011_Real.pdf")) == 1
    assert not any("zz.nii" in f for f in files)
    assert st["pruned"].get("#recycle") == 1
    assert st["pruned"].get("@Recycle") == 1


def test_safe_walk_tolerates_permission_error(tmp_path):
    """N6-2: 'Temp_188_BRL' is EACCES even as `csnl`; an unguarded walk raises
    and the whole ingest aborts. safe_walk records it and keeps going."""
    root = _mk_tree(tmp_path)
    denied = root / "Temp_188_BRL_local"        # not in the prune list
    denied.mkdir()
    (denied / "secret.txt").write_text("x", encoding="utf-8")
    os.chmod(denied, 0o000)
    seen_errors = []
    try:
        if os.access(denied, os.R_OK):          # running as root: cannot simulate
            pytest.skip("cannot revoke read permission as this user")
        st = C.new_walk_stats()
        walked = list(C.safe_walk(root, stats=st, on_error=seen_errors.append))
        files = [f for _, _, fs in walked for f in fs]
        assert "Smith_2011_Real.pdf" in files   # the walk completed
        assert st["errors_n"] == 1
        assert isinstance(seen_errors[0], PermissionError)
        assert "secret.txt" not in files
    finally:
        os.chmod(denied, stat.S_IRWXU)


def test_safe_walk_error_callback_failure_does_not_kill_the_walk(tmp_path):
    root = _mk_tree(tmp_path)
    denied = root / "denied"
    denied.mkdir()
    os.chmod(denied, 0o000)
    try:
        if os.access(denied, os.R_OK):
            pytest.skip("cannot revoke read permission as this user")

        def boom(_exc):
            raise RuntimeError("callback exploded")

        walked = list(C.safe_walk(root, on_error=boom))
        assert any(f == "Smith_2011_Real.pdf" for _, _, fs in walked for f in fs)
    finally:
        os.chmod(denied, stat.S_IRWXU)


def test_safe_walk_missing_root_is_not_fatal(tmp_path):
    st = C.new_walk_stats()
    assert list(C.safe_walk(tmp_path / "nope", stats=st)) == []
    assert st["errors_n"] == 1


def test_safe_walk_max_depth(tmp_path):
    root = _mk_tree(tmp_path)
    d0, _ = _walk(root, max_depth=0)
    assert [d for d, _, _ in d0] == [str(root)]
    d1, _ = _walk(root, max_depth=1)
    assert str(root / "Memory") in [d for d, _, _ in d1]
    assert str(root / "Memory" / "Papers") not in [d for d, _, _ in d1]


def test_safe_walk_shallow_lists_but_does_not_recurse(tmp_path):
    """never_walk_deep semantics: session dir NAMES are visible, the tens of
    thousands of DICOMs behind them are never touched."""
    root = _mk_tree(tmp_path)
    dicom = root / "fMRI_DICOM"
    (dicom / "S01" / "run1").mkdir(parents=True)
    (dicom / "S01" / "run1" / "0001.dcm").write_text("x", encoding="utf-8")
    walked, st = _walk(root, shallow={"fMRI_DICOM"})
    dirs = [d for d, _, _ in walked]
    assert str(dicom) in dirs                       # listed ...
    assert str(dicom / "S01") not in dirs           # ... but not recursed
    assert [dn for d, dn, _ in walked if d == str(dicom)][0] == ["S01"]
    assert st["shallow_stopped"].get("fMRI_DICOM") == 1
    assert not any("0001.dcm" in f for _, _, fs in walked for f in fs)


def test_safe_walk_shallow_can_be_disabled(tmp_path):
    root = _mk_tree(tmp_path)
    (root / "fMRI_DICOM" / "S01").mkdir(parents=True)
    dirs = [d for d, _, _ in C.safe_walk(root, shallow=())]
    assert str(root / "fMRI_DICOM" / "S01") in dirs


def test_safe_walk_honours_in_place_dirnames_pruning(tmp_path):
    """os.walk compatibility — callers already write `dirs[:] = [...]`."""
    root = _mk_tree(tmp_path)
    visited = []
    for dirpath, dirnames, _ in C.safe_walk(root):
        visited.append(dirpath)
        dirnames[:] = [d for d in dirnames if d != "Memory"]
    assert str(root / "Memory") not in visited
    assert str(root / "GRM") in visited


def test_safe_walk_extra_prune(tmp_path):
    root = _mk_tree(tmp_path)
    dirs = [d for d, _, _ in C.safe_walk(root, extra_prune=("GRM",))]
    assert not any("GRM" in d for d in dirs)


def test_safe_walk_prune_matching_is_nfc_aware(tmp_path):
    """The catalog stores NFC; the share stores NFD. A prune entry must still
    match — this is the catalog's own 'PACS설치 파일' demonstration."""
    root = tmp_path / "share2"
    root.mkdir()
    on_disk = unicodedata.normalize("NFD", "PACS설치 파일")
    (root / on_disk).mkdir()
    (root / on_disk / "inner.txt").write_text("x", encoding="utf-8")
    (root / "keep").mkdir()
    catalog_name = unicodedata.normalize("NFC", "PACS설치 파일")
    st = C.new_walk_stats()
    walked = list(C.safe_walk(root, extra_prune=(catalog_name,), stats=st))
    assert not any("inner.txt" in fs for _, _, fs in walked)
    assert st["pruned"].get(C.nfc(catalog_name)) == 1
    assert any(d.endswith("keep") for d, _, _ in walked)


def test_safe_walk_yields_raw_names_that_are_openable(tmp_path):
    """safe_walk must NOT normalise the names it yields: the raw bytes are what
    open()/stat() need on a remote share. nfc() is for comparison only."""
    root = tmp_path / "share3"
    root.mkdir()
    raw = unicodedata.normalize("NFD", "Barabási_2012_Network.pdf")
    (root / raw).write_bytes(b"x")
    listed = os.listdir(root)
    walked = list(C.safe_walk(root))
    names = [f for _, _, fs in walked for f in fs]
    assert names == listed                       # byte-identical to the FS
    for d, _, fs in walked:
        for f in fs:
            assert os.path.exists(os.path.join(d, f))
    if unicodedata.normalize("NFC", listed[0]) != listed[0]:
        # tmpfs preserved NFD (as SMB does) -> prove we did not re-compose it
        assert names[0] != unicodedata.normalize("NFC", names[0])


def test_safe_walk_has_no_follow_symlinks_knob():
    """Enabling symlink following is the bug; there must be no way to ask."""
    import inspect
    params = inspect.signature(C.safe_walk).parameters
    assert "follow_symlinks" not in params
    assert "followlinks" not in params
    src = inspect.getsource(C.safe_walk)
    assert "follow_symlinks=False" in src        # explicit on the is_dir probe


def test_stats_lists_are_capped(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(C._STATS_LIST_CAP + 25):
        os.symlink(str(root), str(root / f"l{i:03d}"))
    st = C.new_walk_stats()
    list(C.safe_walk(root, stats=st))
    assert st["symlinks_skipped_n"] == C._STATS_LIST_CAP + 25
    assert len(st["symlinks_skipped"]) == C._STATS_LIST_CAP


# =========================================================================
# (iv) OPT-IN live check against the real share  (CSNL_NAS_LIVE=1)
# =========================================================================

def _resolve_nas_base():
    """Catalog mount.resolve_rule: probe /Volumes for a readable directory that
    contains BOTH 'GRM/' and 'MM/'. NEVER hardcode a volume name, NEVER mount."""
    vols = Path("/Volumes")
    if not vols.is_dir():
        return None
    for entry in sorted(vols.iterdir()):
        try:
            if not entry.is_dir():
                continue
            names = {C.nfc(n) for n in os.listdir(entry)}
        except OSError:
            continue
        if {"GRM", "MM"} <= names:
            return entry
    return None


@pytest.mark.skipif(os.environ.get("CSNL_NAS_LIVE") != "1",
                    reason="live NAS walk is opt-in (CSNL_NAS_LIVE=1); the share "
                           "tolerates few readers and must never be re-mounted")
def test_live_bounded_walk_does_not_follow_the_share_root_symlink():
    base = _resolve_nas_base()
    if base is None:
        pytest.skip("NAS share not mounted (do NOT mount from an agent)")
    link = base / "CSNL_new-1"
    if not link.is_symlink():
        pytest.skip("share root no longer carries the self-symlink")
    assert os.path.realpath(link) == os.path.realpath(base)      # it IS the loop

    st = C.new_walk_stats()
    walked = list(C.safe_walk(base, max_depth=1, stats=st))       # bounded: 1 level
    dirs = [d for d, _, _ in walked]
    assert len(dirs) == len(set(dirs))                            # no double-count
    # NOTE: the MOUNT POINT itself is named '/Volumes/CSNL_new-1', i.e. its
    # basename collides with the symlink's name. Compare RELATIVE paths, or a
    # basename filter rejects the walk root. (This bit the first draft of this
    # very test — the same trap any consumer-side filter would hit.)
    rel = [os.path.relpath(d, base) for d in dirs]
    assert "." in rel                                             # the root was walked
    assert not any(p.split(os.sep)[0] == "CSNL_new-1" for p in rel if p != ".")
    for _, dirnames, _ in walked:
        keys = [C.nfc(x) for x in dirnames]
        assert "CSNL_new-1" not in keys                           # never even offered
        assert "#recycle" not in keys
        assert "@Recycle" not in keys
    assert st["symlinks_skipped_n"] >= 1
    assert st["pruned"], "no prune fired on the real share root"


@pytest.mark.skipif(os.environ.get("CSNL_NAS_LIVE") != "1",
                    reason="live NAS read is opt-in (CSNL_NAS_LIVE=1)")
def test_live_memory_papers_are_nfd_and_nfc_restores_conformance():
    """One bounded listing of Memory/Papers (the N6-3 measurement), asserted
    against the real corpus: names ARE stored NFD, and NFC lifts author-year
    regex conformance from ~98% to ~99.9%."""
    base = _resolve_nas_base()
    if base is None:
        pytest.skip("NAS share not mounted (do NOT mount from an agent)")
    papers = base / "Memory" / "Papers"
    if not papers.is_dir():
        pytest.skip("Memory/Papers not present")
    walked = list(C.safe_walk(papers, max_depth=0))                # exactly 1 scandir
    names = [f for _, _, fs in walked for f in fs if f.lower().endswith(".pdf")]
    assert len(names) > 1000, "unexpectedly small corpus — check the mount"
    non_nfc = [n for n in names if C.nfc(n) != n]
    assert non_nfc, "expected NFD-stored filenames on the share"
    raw_ok = sum(1 for n in names if PAPER_RE.match(n))
    nfc_ok = sum(1 for n in names if PAPER_RE.match(C.nfc(n)))
    assert nfc_ok > raw_ok
    assert nfc_ok / len(names) > 0.99                              # catalog: 99.94%
    assert nfc_ok - raw_ok >= len(non_nfc) - 5                     # NFD == the misses
    print(f"\n[live] Memory/Papers pdfs={len(names)} non_nfc={len(non_nfc)} "
          f"raw={raw_ok}/{len(names)}={raw_ok/len(names):.4%} "
          f"nfc={nfc_ok}/{len(names)}={nfc_ok/len(names):.4%}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
