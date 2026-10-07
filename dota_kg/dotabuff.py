"""Parse user-saved public Dotabuff HTML. Never fetch or evade HTTP 403."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from .common import RAW, as_int, as_rate


def _table(soup, required):
    for table in soup.find_all("table"):
        headers = " ".join(x.get_text(" ", strip=True).casefold() for x in table.select("thead th"))
        if all(token in headers for token in required):
            return table, headers
    return None, ""


def _rows(table):
    return table.select("tbody tr") if table else []


def _cell(cells, labels, *tokens):
    index = next((i for i, label in enumerate(labels) if all(token in label for token in tokens)), None)
    return cells[index] if index is not None and index < len(cells) else None


def _linked_name(row, entity):
    link = row.find("a", href=re.compile(rf"/{entity}/"))
    if link is None:
        return None
    text = link.get_text(" ", strip=True) or link.get("title")
    if not text and link.find("img"):
        text = link.find("img").get("alt")
    return text


def parse_counters(html: str, hero_id: int, mapper, retrieved_at: str):
    soup = BeautifulSoup(html, "html.parser")
    table, headers = _table(soup, ("hero", "win rate", "matches"))
    if table is None:
        raise ValueError("Counters page has no full Matchups table with hero win rate and matches")
    if "disadvantage" in headers:
        direction = -1
    elif "advantage" in headers:
        direction = 1
    else:
        raise ValueError("Advantage direction is not labelled in the table")
    labels = [th.get_text(" ", strip=True).casefold() for th in table.select("thead th")]
    observations = []
    for tr in _rows(table):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        opponent_name = _linked_name(tr, "heroes")
        if not opponent_name:
            hero_cell = next((cells[i] for i, label in enumerate(labels) if "hero" in label and "win" not in label and i < len(cells)), None)
            opponent_name = hero_cell.get_text(" ", strip=True) if hero_cell else None
        opponent_id = mapper.resolve("hero", display_name=opponent_name, source="dotabuff")
        if not opponent_id or opponent_id == hero_id:
            continue
        advantage_cell = _cell(cells, labels, "disadvantage") if direction == -1 else _cell(cells, labels, "advantage")
        rate_cell = _cell(cells, labels, "win", "rate")
        matches_cell = _cell(cells, labels, "matches")
        raw_advantage = as_rate(advantage_cell.get_text(" ", strip=True)) if advantage_cell else None
        rate = as_rate(rate_cell.get_text(" ", strip=True)) if rate_cell else None
        matches = as_int(matches_cell.get_text(" ", strip=True)) if matches_cell else None
        observations.append({
            "source": "dotabuff", "hero_id": hero_id, "opponent_hero_id": opponent_id,
            "matches": matches, "wins": None, "win_rate": rate,
            "source_advantage": raw_advantage,
            "normalized_advantage": direction * raw_advantage if raw_advantage is not None else None,
            "hero_position": None, "opponent_position": None, "rank_bracket": None,
            "retrieved_at": retrieved_at,
        })
    return observations


def parse_items(html: str, hero_id: int, mapper, retrieved_at: str):
    soup = BeautifulSoup(html, "html.parser")
    table, headers = _table(soup, ("item", "win rate", "matches"))
    if table is None:
        return []
    labels = [th.get_text(" ", strip=True).casefold() for th in table.select("thead th")]
    result = []
    for tr in _rows(table):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        name = _linked_name(tr, "items")
        if not name:
            item_cell = _cell(cells, labels, "item")
            name = item_cell.get_text(" ", strip=True) if item_cell else None
        item_id = mapper.resolve("item", display_name=name, source="dotabuff")
        if not item_id:
            continue
        matches_cell = _cell(cells, labels, "matches")
        rate_cell = _cell(cells, labels, "win", "rate")
        matches = as_int(matches_cell.get_text(" ", strip=True)) if matches_cell else None
        rate = as_rate(rate_cell.get_text(" ", strip=True)) if rate_cell else None
        result.append({
            "source": "dotabuff", "hero_id": hero_id, "item_id": item_id,
            "position": None, "phase": None, "matches": matches, "wins": None,
            "win_rate": rate, "usage_count": None, "usage_rate": None,
            "average_purchase_time": None, "rank_bracket": None, "retrieved_at": retrieved_at,
        })
    return result


def parse_hero_meta(html: str, mapper, retrieved_at: str):
    soup = BeautifulSoup(html, "html.parser")
    table, _ = _table(soup, ("hero", "win rate"))
    if table is None:
        raise ValueError("Hero statistics page has no hero / win rate table")
    labels = [th.get_text(" ", strip=True).casefold() for th in table.select("thead th")]
    result = []
    for tr in _rows(table):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < len(labels):
            continue
        values = dict(zip(labels, (cell.get_text(" ", strip=True) for cell in cells)))
        hero_label = next((v for k, v in values.items() if "hero" in k), None)
        hid = mapper.resolve("hero", display_name=hero_label, source="dotabuff")
        if not hid:
            continue
        def field(*parts):
            return next((v for k, v in values.items() if all(part in k for part in parts)), None)
        result.append({
            "source": "dotabuff", "hero_id": hid, "matches": as_int(field("matches")),
            "wins": None, "win_rate": as_rate(field("win", "rate")),
            "pick_count": as_int(field("picks")), "pick_rate": as_rate(field("pick", "rate")),
            "ban_count": as_int(field("bans")), "ban_rate": as_rate(field("ban", "rate")),
            "professional_matches": None, "professional_wins": None, "professional_bans": None,
            "rank_brackets": None, "position": None, "rank_bracket": None,
            "retrieved_at": retrieved_at, "raw": values,
        })
    return result


def _classify(soup, path: Path):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    combined = f"{title} {heading} {path.stem}".casefold()
    kind = "counters" if "counter" in combined else "items" if "items" in combined else "meta"
    if kind == "meta":
        return kind, None
    match = re.search(r"(.+?)\s*[-–|:]?\s*(?:counters|items)\b", heading, re.I)
    if not match:
        match = re.search(r"(.+?)\s*[-–|:]\s*(?:counters|items)\b", title, re.I)
    if match:
        return kind, match.group(1).strip()
    stem = re.sub(r"[-_ ]?(?:counters|items)$", "", path.stem, flags=re.I)
    return kind, stem.replace("-", " ").replace("_", " ")


def normalize(mapper):
    root = RAW / "dotabuff" / "html"
    meta, matchups, items, errors = [], [], [], []
    for path in sorted(root.rglob("*.html")) if root.exists() else []:
        try:
            html = path.read_text(encoding="utf-8")
            kind, hero_name = _classify(BeautifulSoup(html, "html.parser"), path)
            saved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            if kind == "meta":
                meta.extend(parse_hero_meta(html, mapper, saved_at))
                continue
            hero_id = mapper.resolve("hero", display_name=hero_name, source="dotabuff")
            if not hero_id:
                continue
            if kind == "counters":
                matchups.extend(parse_counters(html, hero_id, mapper, saved_at))
            else:
                items.extend(parse_items(html, hero_id, mapper, saved_at))
        except Exception as error:
            errors.append({"file": str(path), "error": str(error)})
    return meta, matchups, items, errors
