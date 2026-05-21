#!/usr/bin/env python3
"""
scripts/archive/compute_embeddings.py — compute one embedding per archive
paper from title + abstract + venue, write to archive_paper_embeddings.

Backend is env-driven so the operator can pick what's installed locally:

  CSNL_EMBED_BACKEND=local   (default) BAAI/bge-m3 via sentence-transformers
                             1024-dim, multilingual (Korean + English).
                             Requires `pip install sentence-transformers torch`
                             in a venv compatible with your torch wheel
                             (the lab's system Python 3.14 has no torch
                             wheel yet — use a Python 3.11/3.12 venv).
  CSNL_EMBED_BACKEND=voyage  Voyage AI HTTP (VOYAGE_API_KEY). 1024-dim.
                             Use only with explicit operator approval —
                             lab policy is no-LLM-key by default.
  CSNL_EMBED_BACKEND=jina    Jina HTTP (JINA_API_KEY). 1024-dim.
  CSNL_EMBED_BACKEND=openai  OpenAI text-embedding-3-large (3072-dim).

Operator-run (default: dry-run JSONL):
    ! python scripts/archive/compute_embeddings.py            # dry-run JSONL
    ! python scripts/archive/compute_embeddings.py --apply    # also write DB
    ! python scripts/archive/compute_embeddings.py --only-missing  # idempotent re-run

Reads state/archive/merged_papers.jsonl + filter_decisions.jsonl, embeds
only is_lab_relevant=true papers (the rest never reach a queue), writes
state/archive/embeddings.jsonl + (optionally) UPSERTs to DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import kst_iso  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE   = _REPO_ROOT / "state" / "archive"
_IN_PAPERS = _ARCHIVE / "merged_papers.jsonl"
_IN_FILTERS = _ARCHIVE / "filter_decisions.jsonl"
_OUT       = _ARCHIVE / "embeddings.jsonl"

DEFAULT_BACKEND = os.environ.get("CSNL_EMBED_BACKEND", "local")
DEFAULT_MODEL_LOCAL = os.environ.get("CSNL_EMBED_MODEL", "BAAI/bge-m3")


# -------------------------------------------------------------- compose text

def compose_text(p: dict) -> str:
    parts = [p.get("title") or "", p.get("abstract") or "", p.get("venue") or ""]
    if p.get("authors_json"):
        parts.append("; ".join(p["authors_json"][:6]))
    return "\n".join(s for s in parts if s).strip()


# ------------------------------------------------------------- backends

class _LocalBackend:
    """sentence-transformers BAAI/bge-m3 (1024-dim, multilingual)."""
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy import
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np
        v = self.model.encode(texts, batch_size=16, normalize_embeddings=True,
                              show_progress_bar=False)
        return [list(map(float, x)) for x in np.asarray(v)]

    @property
    def dim(self) -> int:
        # bge-m3 dense = 1024; if a custom model is configured, infer from a probe.
        if self.model_name == "BAAI/bge-m3":
            return 1024
        v = self.model.encode(["probe"], normalize_embeddings=True)
        return int(len(v[0]))


class _VoyageBackend:
    """Voyage AI HTTP (voyage-3, 1024-dim)."""
    def __init__(self, model_name: str = "voyage-3"):
        self.model_name = model_name
        self._api_key = os.environ.get("VOYAGE_API_KEY")
        if not self._api_key:
            raise RuntimeError("VOYAGE_API_KEY missing")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import requests
        r = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            json={"input": texts, "model": self.model_name, "input_type": "document"},
            timeout=60,
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]

    @property
    def dim(self) -> int:
        return 1024


class _JinaBackend:
    """Jina HTTP (jina-embeddings-v3, 1024-dim)."""
    def __init__(self, model_name: str = "jina-embeddings-v3"):
        self.model_name = model_name
        self._api_key = os.environ.get("JINA_API_KEY")
        if not self._api_key:
            raise RuntimeError("JINA_API_KEY missing")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import requests
        r = requests.post(
            "https://api.jina.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            json={"input": texts, "model": self.model_name, "task": "retrieval.passage"},
            timeout=60,
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]

    @property
    def dim(self) -> int:
        return 1024


class _OpenAIBackend:
    """OpenAI text-embedding-3-large (3072-dim)."""
    def __init__(self, model_name: str = "text-embedding-3-large"):
        self.model_name = model_name
        self._api_key = os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY missing")

    def encode(self, texts: list[str]) -> list[list[float]]:
        import requests
        r = requests.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json"},
            json={"input": texts, "model": self.model_name},
            timeout=60,
        )
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]

    @property
    def dim(self) -> int:
        return 3072


_REMOTE_BACKENDS = ("voyage", "jina", "openai")
# state/.ARCHIVE_EMBED_APPROVED is an operator-placed token file authorising
# remote-API embeddings (Voyage / Jina / OpenAI). Without it, --backend must
# stay 'local' — this preserves the lab's no-LLM-API-keys-in-unattended-code
# policy (CLAUDE.md + rules/00_scope.md). The contents of the file are not
# inspected; mere existence + a CLI co-confirmation are the gate.
_REMOTE_APPROVAL_TOKEN_PATH = (
    Path(__file__).resolve().parent.parent.parent / "state" / ".ARCHIVE_EMBED_APPROVED"
)


def _assert_remote_approved(name: str, operator_approved: bool) -> None:
    if name == "local":
        return
    if name not in _REMOTE_BACKENDS:
        return
    if not operator_approved:
        raise RuntimeError(
            f"Backend {name!r} sends paper text to a third-party API. "
            f"Pass --operator-approved-remote-embed AND create "
            f"{_REMOTE_APPROVAL_TOKEN_PATH} (touch it; contents ignored) "
            f"to authorise. This gate exists per CLAUDE.md / rules/00."
        )
    if not _REMOTE_APPROVAL_TOKEN_PATH.exists():
        raise RuntimeError(
            f"Backend {name!r} requires an on-disk operator approval "
            f"token at {_REMOTE_APPROVAL_TOKEN_PATH} (currently missing). "
            f"Create it with `touch {_REMOTE_APPROVAL_TOKEN_PATH}` and "
            f"re-run."
        )


def _make_backend(name: str):
    if name == "local":
        return _LocalBackend(DEFAULT_MODEL_LOCAL)
    if name == "voyage":
        return _VoyageBackend()
    if name == "jina":
        return _JinaBackend()
    if name == "openai":
        return _OpenAIBackend()
    raise RuntimeError(f"Unknown CSNL_EMBED_BACKEND={name!r}")


# ----------------------------------------------------------------- IO

def _read_papers() -> list[dict]:
    rows = []
    for line in _IN_PAPERS.read_text("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _read_filters() -> dict:
    out = {}
    if not _IN_FILTERS.exists():
        return out
    for line in _IN_FILTERS.read_text("utf-8").splitlines():
        if line.strip():
            f = json.loads(line)
            out[f["canonical_id"]] = f
    return out


def _existing_cids_in_db(schema: str, model_name: str) -> set[str]:
    sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
    from _db import query_json
    rows = query_json(
        f"SELECT canonical_id FROM {schema}.archive_paper_embeddings "
        f"WHERE model_name = '{model_name}'"
    )
    return {r["canonical_id"] for r in rows}


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default=DEFAULT_BACKEND,
                    choices=("local", "voyage", "jina", "openai"))
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-missing", action="store_true",
                    help="Skip canonical_ids already in archive_paper_embeddings.")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--operator-approved-remote-embed", action="store_true",
                    dest="operator_approved_remote_embed",
                    help="Required when --backend is voyage/jina/openai. "
                         "Must be paired with the on-disk approval token "
                         "(see _assert_remote_approved). Local backend "
                         "does not require this flag.")
    args = ap.parse_args()

    _assert_remote_approved(args.backend, args.operator_approved_remote_embed)

    if not _IN_PAPERS.exists():
        print(f"ERROR: {_IN_PAPERS} not found (run merge_dedupe_filter.py first)",
              file=sys.stderr)
        return 2

    papers = _read_papers()
    filters = _read_filters()
    in_scope = [p for p in papers
                if filters.get(p["canonical_id"], {}).get("is_lab_relevant", True)]
    print(f"[embed] papers={len(papers)}  in_scope={len(in_scope)}  backend={args.backend}")

    # Backend instantiation will fail fast if the env isn't ready — that's intentional.
    backend = _make_backend(args.backend)
    model_name = getattr(backend, "model_name",
                         DEFAULT_MODEL_LOCAL if args.backend == "local" else args.backend)
    dim = backend.dim
    print(f"[embed] model={model_name}  dim={dim}")

    target = in_scope
    if args.only_missing and args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema  # noqa: E402
        load_env()
        sch = ledger_schema()
        have = _existing_cids_in_db(sch, model_name)
        target = [p for p in target if p["canonical_id"] not in have]
        print(f"[embed] existing in DB: {len(have)}  remaining: {len(target)}")

    if args.limit:
        target = target[: args.limit]

    # Pre-filter: skip papers whose composed text is empty/whitespace —
    # encoding empty strings yields near-zero vectors that contaminate
    # cosine similarity in the queue builder.
    skipped_empty = 0
    workable = []
    for p in target:
        if (compose_text(p) or "").strip():
            workable.append(p)
        else:
            skipped_empty += 1
    if skipped_empty:
        print(f"[embed] skipping {skipped_empty} papers with empty composed text")
    target = workable

    out_rows: list[dict] = []
    t0 = time.time()
    for i in range(0, len(target), args.batch):
        batch = target[i : i + args.batch]
        texts = [compose_text(p) for p in batch]
        vecs = backend.encode(texts)
        ts = kst_iso()
        for p, v in zip(batch, vecs):
            out_rows.append({
                "canonical_id": p["canonical_id"],
                "model_name":   model_name,
                "dim":          dim,
                "embedding_json": v,
                "generated_at": ts,
            })
        if (i // args.batch) % 5 == 0:
            done = min(i + args.batch, len(target))
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            print(f"[embed] {done}/{len(target)}  rate={rate:.1f}/s")

    _ARCHIVE.mkdir(parents=True, exist_ok=True)
    with _OUT.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[embed] wrote {_OUT.relative_to(_REPO_ROOT)}  rows={len(out_rows)}")

    if args.apply:
        sys.path.insert(0, str(_REPO_ROOT / "pipeline"))
        from _db import load_env, ledger_schema, exec_many  # noqa: E402
        load_env()
        sch = ledger_schema()
        sql = f"""
            INSERT INTO {sch}.archive_paper_embeddings
              (canonical_id, model_name, dim, embedding_json, generated_at)
            VALUES (%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (canonical_id, model_name) DO UPDATE SET
              dim = EXCLUDED.dim,
              embedding_json = EXCLUDED.embedding_json,
              generated_at   = EXCLUDED.generated_at;
        """
        n = exec_many(sql, [
            (r["canonical_id"], r["model_name"], r["dim"],
             json.dumps(r["embedding_json"]), r["generated_at"])
            for r in out_rows
        ])
        print(f"[embed] DB UPSERTed: {n}")
    else:
        print("[embed] dry-run only. Re-run with --apply to UPSERT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
