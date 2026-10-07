from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
FINAL = DATA / "final"
REPORTS = DATA / "reports"
USER_AGENT = "Dota2CounterpickKnowledgeGraph/0.1 (university research)"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        out.write("\n")
        temporary = Path(out.name)
    os.replace(temporary, path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def fetch(url: str, *, method="GET", payload=None, token=None, timeout=30):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(payload).encode()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = min(120, int(retry_after)) if retry_after and retry_after.isdigit() else min(60, 2 ** (attempt + 1))
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(min(30, 2 ** (attempt + 1)))
    raise RuntimeError("unreachable")


def fetch_json(url: str, **kwargs):
    body, _ = fetch(url, **kwargs)
    return json.loads(body)


@contextmanager
def snapshot(source: str):
    """Keep the previous source snapshot if a collection run fails."""
    RAW.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{source}-", dir=RAW))
    try:
        yield staging
        target = RAW / source
        backup = RAW / f".{source}-previous"
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.rename(backup)
        staging.rename(target)
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def clean_text(value) -> str | None:
    if value is None:
        return None
    from bs4 import BeautifulSoup
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return " ".join(text.split()) or None


def ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def numeric(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def as_int(value):
    n = numeric(value)
    return int(n) if n is not None else None


def as_rate(value):
    if value is None:
        return None
    s = str(value).strip()
    n = numeric(s.rstrip("%"))
    return n / 100 if n is not None and "%" in s else n
