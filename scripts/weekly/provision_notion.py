#!/usr/bin/env python3
"""
scripts/weekly/provision_notion.py — bring the two P23 Notion databases up to
the schema send_notion.py / capture_responses.py expect (design §5).

The operator creates the two databases under the "CSNL 논문 추천" page and shares
them with the integration; this script then adds the missing properties via the
Notion API so the property NAMES + TYPES match the code exactly (no manual
property-by-property setup, and no name-mismatch debugging).

Idempotent: re-running makes no changes once provisioned. Non-destructive — it
only renames the default title property to "Title" and ADDS missing properties;
it never deletes or retypes an existing property (it warns instead).

The response property ("Status") is provisioned as a SELECT, not a Notion
`status` type: the API cannot set custom options on a real status property, but
a select takes the exact Korean labels. capture_responses reads either type.

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
    # Response (Status) option labels — exactly the keys capture maps from,
    # plus the pending default for the digest DB.
    response_opts = list(N.status_choice_map().keys())  # 📚저장 / ❌관련없음 / ✅이미읽음
    pending = N.status_pending_label()                  # 미응답

    digest = {
        # non-title properties only; the title prop is renamed to "Title".
        "Researcher":     {"type": "select", "options": inits},
        "Week":           {"type": "rich_text"},
        "Tier":           {"type": "select", "options": ["S", "A", "B", "C"]},
        "Status":         {"type": "select", "options": [pending] + response_opts,
                           "response": True},
        "Recommendation": {"type": "rich_text"},
        "DOI":            {"type": "url"},
        "Sent At":        {"type": "date"},
        "canonical_id":   {"type": "rich_text"},
    }
    history = {
        "Researcher":     {"type": "select", "options": inits},
        "Status":         {"type": "select", "options": response_opts,
                           "response": True},
        "Week":           {"type": "rich_text"},
        "DOI":            {"type": "url"},
        "canonical_id":   {"type": "rich_text"},
        "Responded At":   {"type": "date"},
    }
    return {"digest": digest, "history": history}


def _add_body(ptype: str, spec: dict) -> dict:
    if ptype == "select":
        return {"select": {"options": [{"name": o} for o in spec["options"]]}}
    if ptype == "rich_text":
        return {"rich_text": {}}
    if ptype == "url":
        return {"url": {}}
    if ptype == "date":
        return {"date": {}}
    raise ValueError(f"unsupported provision type: {ptype}")


def provision(db_id: str, desired: dict, apply: bool) -> int:
    db = N.retrieve_database(db_id)
    props = db.get("properties") or {}
    name_to_type = {n: m.get("type") for n, m in props.items()}
    actions: list[str] = []
    patch: dict = {}

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
        is_response = bool(spec.get("response"))
        if name not in name_to_type:
            patch[name] = _add_body(ptype, spec)
            actions.append(f"add '{name}' ({ptype})"
                           + (f" opts={spec['options']}" if ptype == "select" else ""))
            continue
        got = name_to_type[name]
        # Response prop may already be a select OR a real status — both fine.
        type_ok = (got == ptype) or (is_response and got in ("select", "status"))
        if not type_ok:
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
        elif is_response and got == "status":
            actions.append(f"note: '{name}' is a real status property; cannot "
                           f"set options via API — verify the labels in the UI "
                           f"(need 저장/관련없음/이미읽음 variants).")

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
    for _role, db_id, desired in targets:
        rc |= provision(db_id, desired, args.apply)

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
