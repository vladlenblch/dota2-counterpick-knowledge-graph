"""OpenDota REST collector; observations remain separate from other sources."""
from __future__ import annotations

import time

from .common import RAW, fetch_json, now, ratio, read_json, snapshot, write_json

BASE = "https://api.opendota.com/api"
CONSTANTS = ("heroes", "abilities", "items", "game_mode")
PHASES = {
    "start_game_items": "start", "early_game_items": "early",
    "mid_game_items": "mid", "late_game_items": "late",
}


def collect(delay=1.0):
    with snapshot("opendota") as out:
        heroes = fetch_json(f"{BASE}/heroes")
        stats = fetch_json(f"{BASE}/heroStats")
        write_json(out / "heroes.json", heroes)
        write_json(out / "heroStats.json", stats)
        errors = []
        for name in CONSTANTS:
            try:
                write_json(out / "constants" / f"{name}.json", fetch_json(f"{BASE}/constants/{name}"))
            except Exception as error:
                errors.append({"endpoint": f"constants/{name}", "error": str(error)})
            time.sleep(delay)
        for index, hero in enumerate(heroes, 1):
            hid = hero["id"]
            for endpoint in ("matchups", "itemPopularity"):
                try:
                    write_json(out / endpoint / f"{hid}.json", fetch_json(f"{BASE}/heroes/{hid}/{endpoint}"))
                except Exception as error:
                    errors.append({"endpoint": f"heroes/{hid}/{endpoint}", "error": str(error)})
                time.sleep(delay)
            if index % 25 == 0:
                print(f"OpenDota heroes: {index}/{len(heroes)}", flush=True)
        write_json(out / "manifest.json", {"retrieved_at": now(), "heroes": len(heroes), "errors": errors})
    return {"heroes": len(heroes), "errors": len(errors)}


def normalize():
    stats = read_json(RAW / "opendota" / "heroStats.json", [])
    manifest = read_json(RAW / "opendota" / "manifest.json", {})
    retrieved_at = manifest.get("retrieved_at")
    meta, matchups, hero_items = [], [], []
    baselines = {}
    for row in stats:
        hid = row["id"]
        bracket_rows = []
        for bracket in range(1, 9):
            picks = row.get(f"{bracket}_pick")
            wins = row.get(f"{bracket}_win")
            if picks is not None:
                bracket_rows.append({"rank_bracket": str(bracket), "matches": picks, "wins": wins, "win_rate": ratio(wins, picks)})
        matches = sum(x["matches"] for x in bracket_rows)
        wins = sum(x["wins"] or 0 for x in bracket_rows)
        baselines[hid] = ratio(wins, matches)
        meta.append({
            "source": "opendota", "hero_id": hid, "matches": matches, "wins": wins,
            "win_rate": baselines[hid], "pick_count": matches,
            "pick_rate": None, "ban_count": None, "ban_rate": None,
            "professional_matches": row.get("pro_pick"), "professional_wins": row.get("pro_win"),
            "professional_bans": row.get("pro_ban"), "rank_brackets": bracket_rows,
            "retrieved_at": retrieved_at, "raw": row,
        })
    for hero in read_json(RAW / "opendota" / "heroes.json", []):
        hid = hero["id"]
        for match in read_json(RAW / "opendota" / "matchups" / f"{hid}.json", []):
            opponent = match.get("hero_id")
            if opponent == hid or opponent is None:
                continue
            matches = match.get("games_played")
            wins = match.get("wins")
            rate = ratio(wins, matches)
            base = baselines.get(hid)
            matchups.append({
                "source": "opendota", "hero_id": hid, "opponent_hero_id": opponent,
                "matches": matches, "wins": wins, "win_rate": rate,
                "source_advantage": None,
                "normalized_advantage": rate - base if rate is not None and base is not None else None,
                "hero_position": None, "opponent_position": None, "rank_bracket": None,
                "retrieved_at": retrieved_at,
            })
        popularity = read_json(RAW / "opendota" / "itemPopularity" / f"{hid}.json", {})
        for key, phase in PHASES.items():
            counts = popularity.get(key) or {}
            for item_id, count in counts.items():
                hero_items.append({
                    "source": "opendota", "hero_id": hid, "item_id": int(item_id),
                    "position": None, "phase": phase, "matches": None, "wins": None,
                    "win_rate": None, "usage_count": count, "usage_rate": None,
                    "average_purchase_time": None, "rank_bracket": None,
                    "retrieved_at": retrieved_at,
                })
    return meta, matchups, hero_items
