"""STRATZ GraphQL discovery and saved-response parser.

Queries must be built against a schema obtained with the user's own token.
No unauthenticated or undocumented fallback endpoint is used.
"""
from __future__ import annotations

import os
import urllib.error

from .common import ROOT, RAW, fetch_json, now, read_json, snapshot, write_json

URL = "https://api.stratz.com/graphql"
INTROSPECTION = """query IntrospectionQuery {
  __schema { queryType { name fields { name args { name type { kind name ofType { kind name } } } type { kind name ofType { kind name } } } }
             types { kind name fields { name args { name type { kind name ofType { kind name } } } type { kind name ofType { kind name } } } enumValues { name } } }
}"""


def discover():
    token = os.environ.get("STRATZ_TOKEN")
    if not token and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("STRATZ_TOKEN="):
                token = line.partition("=")[2].strip().strip("\"'")
                break
    if not token:
        raise RuntimeError("STRATZ_TOKEN is not set; STRATZ requires a Bearer token")
    try:
        payload = fetch_json(URL, method="POST", payload={"query": INTROSPECTION}, token=token)
    except urllib.error.HTTPError as error:
        body = error.read(500).decode(errors="replace")
        if error.code == 403 and ("Just a moment" in body or "bearer token is required" in body):
            raise RuntimeError("STRATZ rejected schema introspection with HTTP 403; no automated challenge or access-control bypass is attempted") from None
        raise RuntimeError(f"STRATZ schema request returned HTTP {error.code}") from None
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"]))
    with snapshot("stratz") as out:
        write_json(out / "schema.json", payload)
        write_json(out / "manifest.json", {"retrieved_at": now(), "status": "schema_discovered"})
    return {"types": len(payload["data"]["__schema"]["types"])}


def normalize():
    """No observations are fabricated without a verified current query response."""
    return [], [], [], []
