"""
pipeline/_util.py — shared helpers for all workstream-3 pipeline stages.

All stages import from here. No network I/O, no DB writes, no Anthropic API.
Idiom note: keep in sync with paper_rec_verifier.py comment density.
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_STATE_RUNS = Path(__file__).parent.parent / "state" / "runs"


def run_dir(run_id: str) -> Path:
    """Return (and create) state/runs/<run_id>/."""
    d = _STATE_RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stage_path(run_id: str, name: str) -> Path:
    """Resolve full path for a named stage file inside the run dir."""
    return run_dir(run_id) / name


def load_stage(run_id: str, name: str) -> dict | list:
    """Read a stage JSON file. Raises FileNotFoundError if missing."""
    p = _stage_path(run_id, name)
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump_stage(run_id: str, name: str, obj: dict | list) -> Path:
    """Atomically write obj to state/runs/<run_id>/<name> (temp+rename)."""
    p = _stage_path(run_id, name)
    # Write to sibling temp file, then rename — atomic on POSIX
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=p.parent,
        prefix=f".{name}.tmp.",
        suffix=".json",
        delete=False,
        encoding="utf-8",
    ) as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        tmp = fh.name
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------------------
# DOI utilities
# ---------------------------------------------------------------------------

def doi_normalize(s: str) -> str:
    """Strip https://doi.org/ prefix, doi: prefix, lowercase, trim."""
    s = (s or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower()


# ---------------------------------------------------------------------------
# Fuzzy title equality (difflib — stdlib, no deps)
# ---------------------------------------------------------------------------

def _title_clean(t: str) -> str:
    """Lowercase + collapse whitespace for fuzzy comparison."""
    return re.sub(r"\s+", " ", (t or "").lower().strip())


def fuzzy_title_eq(a: str, b: str) -> bool:
    """Return True if SequenceMatcher ratio >= 0.90 (after cleaning)."""
    from difflib import SequenceMatcher
    ca, cb = _title_clean(a), _title_clean(b)
    if not ca or not cb:
        return False
    ratio = SequenceMatcher(None, ca, cb).ratio()
    return ratio >= 0.90


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

_KST = timezone(timedelta(hours=9))


def kst_now() -> datetime:
    """Current time in Asia/Seoul (UTC+9), timezone-aware."""
    return datetime.now(tz=_KST)


def kst_now_str() -> str:
    """ISO-8601 string of current KST time, e.g. 2026-05-18T14:30:00+09:00."""
    return kst_now().isoformat(timespec="seconds")


def run_id_now() -> str:
    """Generate a RUN_ID = YYYYMMDD-HHMM in KST."""
    return kst_now().strftime("%Y%m%d-%H%M")


# ---------------------------------------------------------------------------
# Date-window check (rules/02_date_filters.md)
# ---------------------------------------------------------------------------
# strict:  journal ≤ 365 d, preprint ≤ 90 d
# relaxed: journal ≤ 730 d (2 y), preprint ≤ 180 d (6 m)
# tier values: "strict" | "relaxed"

_WINDOWS = {
    "strict":  {"journal": 365, "preprint": 90},
    "relaxed": {"journal": 730, "preprint": 180},
}


def within_window(date_iso: str, is_preprint: bool, tier: str) -> bool:
    """Return True if date_iso (YYYY-MM-DD or YYYY-MM) falls within the
    allowed window for the given tier and publication type.

    Partial dates (YYYY-MM) are treated as the 1st of the month.
    Unparseable dates return False (conservative — exclude).
    """
    if not date_iso:
        return False
    # Normalise: accept YYYY-MM-DD, YYYY-MM, YYYY
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            pub = datetime.strptime(date_iso[:len(fmt.replace("%Y","0000").replace("%m","00").replace("%d","00"))], fmt)
            break
        except ValueError:
            continue
    else:
        # Try truncating to first 10 chars (e.g. "2025-03-14T...")
        try:
            pub = datetime.strptime(date_iso[:10], "%Y-%m-%d")
        except ValueError:
            return False

    window_cfg = _WINDOWS.get(tier, _WINDOWS["strict"])
    max_days = window_cfg["preprint"] if is_preprint else window_cfg["journal"]
    now = datetime.utcnow()
    age_days = (now - pub).days
    return 0 <= age_days <= max_days


# ---------------------------------------------------------------------------
# Run-id helper (accept from argv or auto-generate)
# ---------------------------------------------------------------------------

def resolve_run_id(argv: list[str] | None = None) -> str:
    """Return run_id from first CLI arg or auto-generate."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    # Accept --run-id=X or positional
    for arg in args:
        if arg.startswith("--run-id="):
            return arg.split("=", 1)[1]
        if not arg.startswith("-"):
            return arg
    return run_id_now()
