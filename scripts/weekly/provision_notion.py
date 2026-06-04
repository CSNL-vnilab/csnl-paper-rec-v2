#!/usr/bin/env python3
"""
scripts/weekly/provision_notion.py — bring the two P23 Notion databases up to
the schema send_notion.py / capture_responses.py expect (design §5).

The operator creates the two databases under the "CSNL 논문 추천" page and shares
them with the integration; this script then adds the missing properties via the
Notion API so the property NAMES + TYPES match the code exactly (no manual
property-by-property setup, and no name-mismatch debugging).

Idempotent: re-running makes no changes once provisioned. It renames the title
property to "Title", ADDS missing properties, and DROPS the retired legacy ones
(per the _DROPS list); it never retypes an existing property (warns instead).

Schema (P23 follow-up): bibliographic fields are split into Title (paper title) /
저자 / APA. The researcher action is a single 읽음 checkbox (checked = read) — the
old Status select is retired. "논문 리스트" has the same split + a 읽음 checkbox
(no 상태 select).

Usage:
    python3 scripts/weekly/provision_notion.py                 # dry-run, both DBs
    python3 scripts/weekly/provision_notion.py --apply         # write, both DBs
    python3 scripts/weekly/provision_notion.py --db digest --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _notion as N  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _researcher_inits() -> list[str]:
    try:
        import yaml
        d = yaml.safe_load((_REPO_ROOT / "config" / "researchers.yaml")
                           .read_text("utf-8"))
        rs = d.get("researchers") or {}
        return sorted(rs.keys())
    except Exception:
        # Fallback to the known 7 (config absent / yaml missing).
        return ["BHL", "BYL", "JOP", "JYK", "MSY", "SMJ", "SYJ"]


def _desired_schemas() -> dict[str, dict]:
    inits = _researcher_inits()
    # P23 follow-up: bibliographic fields split into Title (paper title, the
    # Notion title prop) + 저자 + APA. Researcher action is a single 읽음
    # checkbox (checked = read). No Status select, no Week column.
    digest = {
        # non-title properties only; the title prop is renamed to "Title".
        "저자":            {"type": "rich_text"},
        "APA":            {"type": "rich_text"},
        "Researcher":     {"type": "select", "options": inits},
        "Tier":           {"type": "select", "options": ["S", "A", "B", "C"]},
        "읽음":            {"type": "checkbox"},
        "발표 예정":        {"type": "checkbox"},
        "Recommendation": {"type": "rich_text"},
        "DOI":            {"type": "url"},
        "Sent At":        {"type": "date"},
        "canonical_id":   {"type": "rich_text"},
    }
    # "논문 리스트" mirrors the interview-result reading list (mirror_history.py).
    # Same Title/저자/APA split; a single 읽음 checkbox (checked = 이미 읽음,
    # unchecked = 읽을 예정). Group the Notion view by 읽음 for collapsible
    # read / to-read sections.
    history = {
        "저자":            {"type": "rich_text"},
        "APA":            {"type": "rich_text"},
        "Researcher":     {"type": "select", "options": inits},
        "읽음":            {"type": "checkbox"},
        "DOI":            {"type": "url"},
        "canonical_id":   {"type": "rich_text"},
    }
    return {"digest": digest, "history": history}


# Properties to REMOVE per DB (legacy columns no longer used). Dropping is safe
# here because both DBs are operator-only (digest is rebuilt; history pages keep
# their other columns when a property is removed).
_DROPS: dict[str, list[str]] = {
    "digest":  ["Status", "Week"],                       # retired response select + weekly tag
    "history": ["상태", "Status", "Week", "Responded At"],  # retired status select + unused cols
}


def _add_body(ptype: str, spec: dict) -> dict:
    if ptype == "select":
        return {"select": {"options": [{"name": o} for o in spec["options"]]}}
    if ptype == "rich_text":
        return {"rich_text": {}}
    if ptype == "url":
        return {"url": {}}
    if ptype == "date":
        return {"date": {}}
    if ptype == "checkbox":
        return {"checkbox": {}}
    raise ValueError(f"unsupported provision type: {ptype}")


def provision(db_id: str, desired: dict, apply: bool, drop: list[str] | None = None) -> int:
    db = N.retrieve_database(db_id)
    props = db.get("properties") or {}
    name_to_type = {n: m.get("type") for n, m in props.items()}
    actions: list[str] = []
    patch: dict = {}

    # 0. Drop legacy properties (setting a property to null removes it). Never
    #    drop the title property.
    for d in (drop or []):
        if d in name_to_type and name_to_type[d] != "title":
            patch[d] = None
            actions.append(f"DROP '{d}' ({name_to_type[d]})")

    # 1. Rename the default title property to "Title" (idempotent).
    title_name = next((n for n, t in name_to_type.items() if t == "title"), None)
    if title_name is None:
        actions.append("WARN: no title property found (cannot rename)")
    elif title_name != "Title":
        patch[title_name] = {"name": "Title"}
        actions.append(f"rename title '{title_name}' -> 'Title'")

    # 2. Add / reconcile each desired property.
    for name, spec in desired.items():
        ptype = spec["type"]
        if name not in name_to_type:
            patch[name] = _add_body(ptype, spec)
            actions.append(f"add '{name}' ({ptype})"
                           + (f" opts={spec['options']}" if ptype == "select" else ""))
            continue
        got = name_to_type[name]
        if got != ptype:
            actions.append(f"WARN: '{name}' is '{got}', want '{ptype}' — left as-is")
            continue
        # Union select options so a partially-set-up DB gets the missing ones.
        if ptype == "select" and got == "select":
            cur = [o.get("name") for o in (props[name]["select"].get("options") or [])]
            missing = [o for o in spec["options"] if o not in cur]
            if missing:
                merged = [{"name": o} for o in cur if o] + [{"name": o} for o in missing]
                patch[name] = {"select": {"options": merged}}
                actions.append(f"add options to '{name}': {missing}")

    title = N.db_title(db)
    if not actions:
        print(f"[provision] {title!r} ({db_id}): already provisioned — no changes.")
        return 0
    print(f"[provision] {title!r} ({db_id}): {len(actions)} change(s):")
    for a in actions:
        print(f"    - {a}")
    if not patch:
        return 0
    if not apply:
        print("[provision] dry-run — re-run with --apply to write.")
        return 0
    N._request("PATCH", f"/databases/{db_id}", json_body={"properties": patch})
    print(f"[provision] applied to {title!r}.")
    # Re-validate the digest DB so the operator sees green immediately.
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", choices=("both", "digest", "history"), default="both")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    N.load_env()

    schemas = _desired_schemas()
    targets = []
    if args.db in ("both", "digest"):
        targets.append(("digest", N.digest_db_id(), schemas["digest"]))
    if args.db in ("both", "history"):
        hid = N.history_db_id()
        if hid:
            targets.append(("history", hid, schemas["history"]))
        else:
            print("[provision] NOTION_HISTORY_DB_ID not set — skipping history DB.")

    rc = 0
    for role, db_id, desired in targets:
        rc |= provision(db_id, desired, args.apply, drop=_DROPS.get(role, []))

    if args.db in ("both", "digest") and args.apply:
        ok, probs = N.validate_digest_db(N.digest_db_id())
        print("[provision] digest schema valid ✔" if ok
              else "[provision] digest schema still has problems:")
        for p in probs:
            print(f"    - {p}")
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
