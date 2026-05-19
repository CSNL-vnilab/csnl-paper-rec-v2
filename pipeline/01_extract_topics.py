#!/usr/bin/env python3
"""
pipeline/01_extract_topics.py — Extract topic bundles from active projects.

Reads:  state/runs/<RID>/01_active_projects.json  (from stage 00)
        config/researchers.yaml                    (unit definitions + channels)
Emits:  state/runs/<RID>/02_topic_bundles.json

No LLM calls — pure extractive/template logic.
Keywords are pulled from project structured fields; gist is a template
synthesis (≤60 words) assembled from project titles and research questions.

Output shape per unit:
  {unit_id, members:[init], display_names:[kr], channel_ids:[Cxxx],
   dm_inits:[init], project_slugs:[str], keywords:[str],
   anchor_dois:[str], gist:str}
"""
import os
import sys
import re
from pathlib import Path

import yaml  # pyyaml

sys.path.insert(0, os.path.dirname(__file__))
from _util import load_stage, dump_stage, doi_normalize, kst_now_str, resolve_run_id

# ---------------------------------------------------------------------------
# Config path helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _load_researchers() -> dict:
    """Load config/researchers.yaml as a dict."""
    p = _REPO_ROOT / "config" / "researchers.yaml"
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Keyword extraction — from purpose, manipulation_variables, connected_graph
# ---------------------------------------------------------------------------

_STOP_WORDS = {
    "the","a","an","and","or","of","in","on","to","for","with","from",
    "is","are","was","were","be","this","that","which","how","what",
    "its","their","using","used","we","our","study","studies","effect",
    "effects","between","across","during","within","both","each","may",
    "can","by","at","as","into","over","under","about","via",
}


def _tokenize(text: str) -> list[str]:
    """Split into lowercase alpha tokens ≥4 chars, strip stop words."""
    tokens = re.findall(r"[a-zA-Z]{4,}", (text or "").lower())
    return [t for t in tokens if t not in _STOP_WORDS]


def _extract_strings(obj, max_depth: int = 4) -> list[str]:
    """Recursively extract all string leaf values from a JSON structure."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        out = []
        for item in obj:
            out.extend(_extract_strings(item, max_depth - 1))
        return out
    if isinstance(obj, dict) and max_depth > 0:
        out = []
        for v in obj.values():
            out.extend(_extract_strings(v, max_depth - 1))
        return out
    return []


def _keywords_from_project(proj: dict) -> list[str]:
    """Extract keyword tokens from purpose, manipulation_variables, connected_graph."""
    sources = []
    # purpose: research_question, hypothesis
    purpose = proj.get("purpose") or {}
    if isinstance(purpose, dict):
        sources.append(purpose.get("research_question", ""))
        sources.append(purpose.get("hypothesis", ""))
    # manipulation_variables: independent_vars, dependent_vars
    mvars = proj.get("manipulation_variables") or {}
    if isinstance(mvars, dict):
        for field in ("independent_vars", "dependent_vars"):
            val = mvars.get(field)
            if val:
                sources.extend(_extract_strings(val))
    # connected_graph: shared_paradigm_with (list of strings or sub-dicts)
    cg = proj.get("connected_graph") or {}
    if isinstance(cg, dict):
        spw = cg.get("shared_paradigm_with")
        if spw:
            sources.extend(_extract_strings(spw))
    # Join and tokenize
    tokens = []
    for s in sources:
        tokens.extend(_tokenize(s))
    return tokens


def _anchor_dois_from_project(proj: dict) -> list[str]:
    """Extract and normalize DOIs from background.prior_studies[].doi."""
    background = proj.get("background") or {}
    prior = []
    if isinstance(background, dict):
        prior = background.get("prior_studies") or []
    dois = []
    for entry in (prior if isinstance(prior, list) else []):
        raw_doi = None
        if isinstance(entry, dict):
            raw_doi = entry.get("doi")
        elif isinstance(entry, str):
            # Sometimes stored as bare DOI string
            raw_doi = entry
        if raw_doi:
            nd = doi_normalize(raw_doi)
            if nd:
                dois.append(nd)
    return dois


# ---------------------------------------------------------------------------
# Gist assembly — extractive, ≤60 words, no LLM
# ---------------------------------------------------------------------------

def _build_gist(projects: list[dict]) -> str:
    """Template gist: list project titles + first available research question."""
    parts = []
    for proj in projects:
        title = proj.get("title", proj.get("project_slug", ""))
        if title:
            parts.append(title.strip())
        purpose = proj.get("purpose") or {}
        if isinstance(purpose, dict):
            rq = purpose.get("research_question", "")
            if rq and len(rq) < 200:
                parts.append(rq.strip())
    raw = " ".join(parts)
    # Truncate to ≤60 words
    words = raw.split()
    if len(words) > 60:
        raw = " ".join(words[:60]) + "…"
    return raw or "(no project description available)"


# ---------------------------------------------------------------------------
# Unit assembly
# ---------------------------------------------------------------------------

def build_units(stage01: dict, researchers_yaml: dict) -> list[dict]:
    """Build topic bundles from stage-01 projects + researchers.yaml units."""
    units_cfg = researchers_yaml.get("units", [])
    researchers = researchers_yaml.get("researchers", {})

    # Index projects by init
    projects_by_init: dict[str, list[dict]] = {}
    for proj in stage01.get("projects", []):
        init = proj.get("init", "")
        projects_by_init.setdefault(init, []).append(proj)

    bundles = []
    for unit_cfg in units_cfg:
        unit_id = unit_cfg["unit_id"]
        members = unit_cfg["members"]  # list of init strings

        # Collect all projects for this unit's members
        unit_projects = []
        for init in members:
            unit_projects.extend(projects_by_init.get(init, []))

        if not unit_projects:
            # No active projects for this unit — emit a placeholder so
            # downstream stages know the unit was evaluated
            print(f"[01_extract_topics] unit {unit_id}: no active projects, skipping")
            continue

        # --- Keywords ---
        token_counts: dict[str, int] = {}
        for proj in unit_projects:
            for tok in _keywords_from_project(proj):
                token_counts[tok] = token_counts.get(tok, 0) + 1
        # Sort by frequency desc, deduplicate, cap at 25
        sorted_tokens = sorted(token_counts, key=lambda k: -token_counts[k])
        keywords = sorted_tokens[:25]

        # --- Anchor DOIs ---
        doi_set: set[str] = set()
        for proj in unit_projects:
            for doi in _anchor_dois_from_project(proj):
                doi_set.add(doi)
        anchor_dois = sorted(doi_set)

        # --- Researcher metadata ---
        display_names = [
            researchers.get(init, {}).get("name", init) for init in members
        ]
        channel_ids = [
            researchers.get(init, {}).get("init_claude_channel", "") for init in members
        ]
        # Filter out blank channels (seniors, etc.)
        channel_ids = [c for c in channel_ids if c]

        # dm_inits = same as members (a DM goes to each member)
        dm_inits = list(members)

        # --- Gist ---
        gist = _build_gist(unit_projects)

        bundles.append({
            "unit_id": unit_id,
            "members": members,
            "display_names": display_names,
            "channel_ids": channel_ids,
            "dm_inits": dm_inits,
            "project_slugs": [p["project_slug"] for p in unit_projects],
            "keywords": keywords,
            "anchor_dois": anchor_dois,
            "gist": gist,
        })

    return bundles


def main():
    run_id = resolve_run_id()
    print(f"[01_extract_topics] run_id={run_id}")

    stage01 = load_stage(run_id, "01_active_projects.json")
    researchers = _load_researchers()

    bundles = build_units(stage01, researchers)
    print(f"[01_extract_topics] built {len(bundles)} unit bundle(s)")
    for b in bundles:
        print(f"  unit={b['unit_id']} members={b['members']} "
              f"projects={len(b['project_slugs'])} keywords={len(b['keywords'])}")

    payload = {
        "run_id": run_id,
        "generated_at": kst_now_str(),
        "units": bundles,
    }

    out = dump_stage(run_id, "02_topic_bundles.json", payload)
    print(f"[01_extract_topics] wrote {out}")


if __name__ == "__main__":
    main()
