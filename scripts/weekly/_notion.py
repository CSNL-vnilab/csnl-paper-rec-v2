#!/usr/bin/env python3
"""
scripts/weekly/_notion.py — shared Notion client + config for the P23 weekly
delivery harness.

Deliberately thin: a `requests`-based wrapper (requests is already a declared
lab dependency; no `notion-client` install needed, which keeps the weekly
cron path portable — same ethos as the rest of csnl-paper-rec). All Notion
access funnels through `_request()` so the 429 / 5xx retry + politeness
throttle lives in exactly one place.

Boundary: Notion is a researcher-facing WRITE target. Like Slack, the scripts
that mutate it (send_notion.py) default to dry-run and only POST under an
explicit `--apply`. This module itself performs no writes on import.

Config (token + DB IDs) is read from the repo .env via pipeline/_db.load_env.
Property names default to the spec and can be overridden without code edits by
dropping config/notion_props.json (see _load_prop_overrides). The researcher
action is a single 읽음 checkbox (P23 follow-up); there is no Status select.

CLI:
    python3 scripts/weekly/_notion.py --discover            # list visible DBs
    python3 scripts/weekly/_notion.py --discover --write    # + write IDs to .env
    python3 scripts/weekly/_notion.py --validate            # check DB schemas
    python3 scripts/weekly/_notion.py --whoami              # integration identity
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
from _db import load_env  # noqa: E402

NOTION_VERSION = "2022-06-28"   # stable; treats database_id as a page parent
NOTION_BASE = "https://api.notion.com/v1"

# Politeness: Notion's published soft limit is ~3 requests/sec/integration.
# A small fixed inter-request delay keeps the 35-row create loop under it
# without needing a token bucket.
_MIN_INTERVAL_S = 0.34
_MAX_RETRIES = 5

# ---------------------------------------------------------------- prop config
# "이번 주 논문 추천" (digest) database properties. Keys are the stable internal
# slugs the scripts use; values are the Notion property NAMES. Override via
# config/notion_props.json. P23 follow-up (2026-06-01): bibliographic fields are
# SPLIT into title / 저자 / APA, and the researcher action is a single 읽음
# checkbox (checked = read) — the old 미응답/저장/관련없음 Status select is retired.
_DEFAULT_DIGEST_PROPS: dict[str, str] = {
    "title":          "Title",          # title     — paper title (verbatim)
    "authors":        "저자",            # rich_text — author list
    "apa":            "APA",            # rich_text — full APA-7 citation
    "researcher":     "Researcher",     # select    — BHL/BYL/...
    "tier":           "Tier",           # select    — S/A/B/C
    "read":           "읽음",            # checkbox  — researcher checks when read
    "recommendation": "Recommendation",  # rich_text — Korean rationale
    "doi":            "DOI",            # url       — paper link
    "sent_at":        "Sent At",        # date      — recommended-at
    "canonical_id":   "canonical_id",   # rich_text — internal index
}

# Expected Notion property TYPE per internal slug (used by validate()).
_DIGEST_PROP_TYPES: dict[str, str] = {
    "title":          "title",
    "authors":        "rich_text",
    "apa":            "rich_text",
    "researcher":     "select",
    "tier":           "select",
    "read":           "checkbox",
    "recommendation": "rich_text",
    "doi":            "url",
    "sent_at":        "date",
    "canonical_id":   "rich_text",
}


def _load_prop_overrides() -> dict:
    """Optional config/notion_props.json: {"digest_props": {...}}.
    Missing file → defaults. Partial file → merge over defaults."""
    p = _REPO_ROOT / "config" / "notion_props.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception as e:  # pragma: no cover - operator misconfig
        print(f"[notion] WARN: could not parse {p}: {e}", file=sys.stderr)
        return {}


def digest_props() -> dict[str, str]:
    ov = _load_prop_overrides().get("digest_props") or {}
    return {**_DEFAULT_DIGEST_PROPS, **ov}


# ------------------------------------------------------------- HTTP transport

class NotionError(RuntimeError):
    pass


def token() -> str:
    load_env()
    t = os.environ.get("NOTION_API_KEY", "").strip()
    if not t:
        raise NotionError(
            "NOTION_API_KEY missing from .env. Add the integration token "
            "(ntn_...) before running any weekly Notion script.")
    return t


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {token()}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


_last_call_ts = 0.0


def _throttle() -> None:
    global _last_call_ts
    try:
        now = time.monotonic()
    except Exception:
        return
    wait = _MIN_INTERVAL_S - (now - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def _request(method: str, path: str, *, json_body: Optional[dict] = None,
             timeout: int = 30) -> dict:
    """One Notion API call with throttle + retry. Honours 429 Retry-After and
    backs off on 5xx. Raises NotionError on a persistent failure."""
    import requests  # local import keeps module import cheap for --help
    url = path if path.startswith("http") else f"{NOTION_BASE}{path}"
    last_err = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        _throttle()
        try:
            resp = requests.request(method, url, headers=_headers(),
                                    json=json_body, timeout=timeout)
        except requests.RequestException as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(2 ** attempt, 16))
            continue
        if resp.status_code == 429:
            # Retry-After is usually integer seconds, but the spec also allows
            # an HTTP-date; float() would raise on that. Parse defensively and
            # fall back to an exponential-ish bounded sleep.
            try:
                retry_after = float(resp.headers.get("Retry-After", "") or "")
            except (TypeError, ValueError):
                retry_after = float(min(2 ** attempt, 10))
            time.sleep(min(max(retry_after, 1.0), 30.0))
            last_err = "429 rate limited"
            continue
        if resp.status_code >= 500:
            last_err = f"{resp.status_code} server error"
            time.sleep(min(2 ** attempt, 16))
            continue
        if resp.status_code >= 400:
            # 4xx (other than 429) are not retryable — surface the body.
            try:
                body = resp.json()
                msg = body.get("message") or resp.text
                code = body.get("code", "")
            except Exception:
                msg, code = resp.text, ""
            raise NotionError(f"Notion {method} {path} -> {resp.status_code} "
                              f"{code}: {msg[:400]}")
        try:
            return resp.json()
        except Exception:
            return {}
    raise NotionError(f"Notion {method} {path} failed after {_MAX_RETRIES} "
                      f"retries: {last_err}")


# --------------------------------------------------------------- API surface

def whoami() -> dict:
    return _request("GET", "/users/me")


def search(query: str = "", object_type: Optional[str] = None,
           page_size: int = 100) -> list[dict]:
    """Search all pages/databases shared with the integration. Paginates."""
    out: list[dict] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"query": query, "page_size": page_size}
        if object_type:
            body["filter"] = {"value": object_type, "property": "object"}
        if cursor:
            body["start_cursor"] = cursor
        j = _request("POST", "/search", json_body=body)
        out.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
        if not cursor:
            break
    return out


def db_title(db: dict) -> str:
    return "".join(t.get("plain_text", "") for t in (db.get("title") or []))


def retrieve_database(db_id: str) -> dict:
    return _request("GET", f"/databases/{db_id}")


def query_database(db_id: str, filter_body: Optional[dict] = None,
                   page_size: int = 100) -> list[dict]:
    out: list[dict] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"page_size": page_size}
        if filter_body:
            body["filter"] = filter_body
        if cursor:
            body["start_cursor"] = cursor
        j = _request("POST", f"/databases/{db_id}/query", json_body=body)
        out.extend(j.get("results", []))
        if not j.get("has_more"):
            break
        cursor = j.get("next_cursor")
        if not cursor:
            break
    return out


def create_page(db_id: str, properties: dict) -> dict:
    return _request("POST", "/pages",
                    json_body={"parent": {"database_id": db_id},
                               "properties": properties})


def retrieve_page(page_id: str) -> dict:
    return _request("GET", f"/pages/{page_id}")


def update_page(page_id: str, properties: dict) -> dict:
    return _request("PATCH", f"/pages/{page_id}",
                    json_body={"properties": properties})


# ----------------------------------------------------- property value builders

def _truncate(s: str, n: int = 2000) -> str:
    """Notion rich_text/title content is capped at 2000 chars per text object."""
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def p_title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": _truncate(text)}}]}


def p_rich_text(text: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": _truncate(text)}}]}


def p_select(name: Optional[str]) -> dict:
    if not name:
        return {"select": None}
    return {"select": {"name": name}}


def p_url(url: Optional[str]) -> dict:
    return {"url": (url or None)}


def p_date(iso: Optional[str]) -> dict:
    if not iso:
        return {"date": None}
    return {"date": {"start": iso}}


def p_checkbox(value: bool) -> dict:
    return {"checkbox": bool(value)}


# ----------------------------------------------------- property value readers

def read_rich_text(page: dict, prop_name: str) -> str:
    prop = (page.get("properties") or {}).get(prop_name) or {}
    rt = prop.get("rich_text") or prop.get("title") or []
    return "".join(t.get("plain_text", "") for t in rt)


def read_select(page: dict, prop_name: str) -> Optional[str]:
    prop = (page.get("properties") or {}).get(prop_name) or {}
    sel = prop.get("select")
    return sel.get("name") if isinstance(sel, dict) else None


def read_checkbox(page: dict, prop_name: str) -> bool:
    prop = (page.get("properties") or {}).get(prop_name) or {}
    return bool(prop.get("checkbox"))


# --------------------------------------------------------------- validation

def validate_digest_db(db_id: str) -> tuple[bool, list[str]]:
    """Introspect the digest DB schema and confirm every required property
    exists with the right type. Returns (ok, problems)."""
    problems: list[str] = []
    try:
        db = retrieve_database(db_id)
    except NotionError as e:
        return False, [f"cannot retrieve database {db_id}: {e}"]
    props = db.get("properties") or {}
    name_to_type = {name: meta.get("type") for name, meta in props.items()}
    wanted = digest_props()
    for slug, want_type in _DIGEST_PROP_TYPES.items():
        pname = wanted[slug]
        if pname not in name_to_type:
            problems.append(f"missing property '{pname}' (slug={slug}, "
                            f"expected type={want_type})")
            continue
        got = name_to_type[pname]
        if got != want_type:
            problems.append(f"property '{pname}' is type '{got}', "
                            f"expected '{want_type}'")
    return (len(problems) == 0), problems


# --------------------------------------------------------------- .env writer

def _write_env_ids(digest_id: Optional[str], history_id: Optional[str]) -> None:
    env_path = _REPO_ROOT / ".env"
    lines = env_path.read_text("utf-8").splitlines()

    def set_key(ls: list[str], key: str, val: str) -> list[str]:
        found = False
        out = []
        for ln in ls:
            if ln.strip().startswith(f"{key}="):
                out.append(f"{key}={val}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"{key}={val}")
        return out

    if digest_id:
        lines = set_key(lines, "NOTION_DIGEST_DB_ID", digest_id)
    if history_id:
        lines = set_key(lines, "NOTION_HISTORY_DB_ID", history_id)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[notion] wrote DB IDs to {env_path}")


# --------------------------------------------------------------- ID accessors

def digest_db_id() -> str:
    load_env()
    v = os.environ.get("NOTION_DIGEST_DB_ID", "").strip()
    if not v:
        raise NotionError("NOTION_DIGEST_DB_ID missing from .env. Run "
                          "`python3 scripts/weekly/_notion.py --discover "
                          "--write` after sharing the databases with the "
                          "integration.")
    return v


def history_db_id() -> Optional[str]:
    load_env()
    return os.environ.get("NOTION_HISTORY_DB_ID", "").strip() or None


# --------------------------------------------------------------------- CLI

def _discover(write: bool, digest_title: str, history_title: str) -> int:
    dbs = search(object_type="database")
    print(f"[notion] integration sees {len(dbs)} database(s):")
    digest_hits, history_hits = [], []
    for d in dbs:
        title = db_title(d)
        did = d.get("id")
        print(f"    {did}  {title!r}")
        if digest_title and digest_title in title:
            digest_hits.append((did, title))
        if history_title and history_title in title:
            history_hits.append((did, title))

    def pick(hits, role):
        if len(hits) == 1:
            print(f"[notion] {role}: matched {hits[0][1]!r} -> {hits[0][0]}")
            return hits[0][0]
        if not hits:
            print(f"[notion] {role}: NO database title contains "
                  f"the expected substring. Share it with the integration "
                  f"and/or pass --digest-title/--history-title.")
        else:
            print(f"[notion] {role}: AMBIGUOUS — {len(hits)} matches: "
                  f"{[h[1] for h in hits]}. Resolve manually.")
        return None

    dig = pick(digest_hits, "digest DB")
    his = pick(history_hits, "history DB")
    if write and (dig or his):
        _write_env_ids(dig, his)
    elif write:
        print("[notion] nothing written (no unambiguous match).")
    return 0 if (dig and his) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Notion helper / discovery CLI.")
    ap.add_argument("--discover", action="store_true",
                    help="List visible databases + try to match the two P23 DBs")
    ap.add_argument("--write", action="store_true",
                    help="With --discover: write matched IDs into .env")
    ap.add_argument("--validate", action="store_true",
                    help="Validate the digest DB schema against the spec")
    ap.add_argument("--whoami", action="store_true",
                    help="Print the integration's identity")
    # Operator's actual DB titles (2026-06-01): "이번 주 논문 추천" / "논문 리스트".
    # Substrings chosen to be unambiguous against the 25 other lab DBs (note
    # "리스트" alone also hits "GRM 발표 순번 리스트", so match "논문 리스트").
    ap.add_argument("--digest-title", default="논문 추천")
    ap.add_argument("--history-title", default="논문 리스트")
    args = ap.parse_args()
    load_env()

    if args.whoami:
        me = whoami()
        print(json.dumps({"name": me.get("name"), "type": me.get("type"),
                          "id": me.get("id")}, ensure_ascii=False))
        return 0
    if args.discover:
        return _discover(args.write, args.digest_title, args.history_title)
    if args.validate:
        ok, probs = validate_digest_db(digest_db_id())
        if ok:
            print("[notion] digest DB schema OK")
            return 0
        print("[notion] digest DB schema problems:")
        for p in probs:
            print(f"    - {p}")
        return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
