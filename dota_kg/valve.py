"""Official current Valve Dota 2 datafeed collector and normalizer."""
from __future__ import annotations

import time
from pathlib import Path

from .common import RAW, clean_text, fetch_json, now, read_json, snapshot, write_json

BASE = "https://www.dota2.com/datafeed"


def _list(endpoint, field):
    payload = fetch_json(f"{BASE}/{endpoint}?language=english")
    return payload["result"]["data"][field], payload


def collect(delay=0.25):
    with snapshot("valve") as out:
        heroes, heroes_payload = _list("herolist", "heroes")
        items, items_payload = _list("itemlist", "itemabilities")
        abilities, abilities_payload = _list("abilitylist", "itemabilities")
        for name, payload in [("herolist", heroes_payload), ("itemlist", items_payload), ("abilitylist", abilities_payload)]:
            write_json(out / f"{name}.json", payload)
        errors = []
        for kind, entries, endpoint, parameter, folder in [
            ("hero", heroes, "herodata", "hero_id", "heroes"),
            ("item", [x for x in items if x.get("name_loc")], "itemdata", "item_id", "items"),
        ]:
            for index, entry in enumerate(entries, 1):
                try:
                    payload = fetch_json(f"{BASE}/{endpoint}?{parameter}={entry['id']}&language=english")
                    write_json(out / folder / f"{entry['id']}.json", payload)
                except Exception as error:
                    errors.append({"kind": kind, "id": entry["id"], "error": str(error)})
                if index % 50 == 0:
                    print(f"Valve {kind}: {index}/{len(entries)}", flush=True)
                time.sleep(delay)
        write_json(out / "manifest.json", {"retrieved_at": now(), "hero_count": len(heroes), "named_item_count": sum(bool(x.get('name_loc')) for x in items), "errors": errors})
    return {"heroes": len(heroes), "items": len(items), "errors": len(errors)}


def _entities(folder: str, key: str):
    for path in sorted((RAW / "valve" / folder).glob("*.json")):
        data = read_json(path, {}).get("result", {}).get("data", {})
        for entity in data.get(key, []):
            yield entity


def _item_effects(raw_description):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw_description or "", "html.parser")
    active, passive = [], []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        title = heading.get_text(" ", strip=True).casefold()
        if not (title.startswith("active:") or title.startswith("passive:")):
            continue
        parts = []
        for sibling in heading.next_siblings:
            if getattr(sibling, "name", None) in ("h1", "h2", "h3"):
                break
            text = sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling).strip()
            if text:
                parts.append(text)
        target = active if title.startswith("active:") else passive
        target.append(f"{heading.get_text(' ', strip=True)} {' '.join(parts)}".strip())
    return " | ".join(active) or None, " | ".join(passive) or None


def normalize():
    hero_rows, ability_rows, item_rows = [], [], []
    for hero in _entities("heroes", "heroes"):
        hid = hero["id"]
        hero_rows.append({
            "hero_id": hid, "internal_name": hero.get("name"), "display_name": hero.get("name_loc"),
            "primary_attribute": {0: "strength", 1: "agility", 2: "intelligence", 3: "universal"}.get(hero.get("primary_attr")),
            "strength": hero.get("str_base"), "strength_gain": hero.get("str_gain"),
            "agility": hero.get("agi_base"), "agility_gain": hero.get("agi_gain"),
            "intelligence": hero.get("int_base"), "intelligence_gain": hero.get("int_gain"),
            "health": hero.get("max_health"), "health_regen": hero.get("health_regen"),
            "mana": hero.get("max_mana"), "mana_regen": hero.get("mana_regen"),
            "armor": hero.get("armor"), "magic_resistance": hero.get("magic_resistance"),
            "movement_speed": hero.get("movement_speed"), "attack_type": {1: "Melee", 2: "Ranged"}.get(hero.get("attack_capability")),
            "attack_range": hero.get("attack_range"), "attack_rate": hero.get("attack_rate"),
            "damage_min": hero.get("damage_min"), "damage_max": hero.get("damage_max"),
            "complexity": hero.get("complexity"), "facets": hero.get("facets"),
            "role_levels": hero.get("role_levels"), "raw": hero,
        })
        seen = set()
        for ability in hero.get("abilities", []) + hero.get("facet_abilities", []):
            if ability.get("id") in seen or not ability.get("name_loc"):
                continue
            seen.add(ability["id"])
            ability_rows.append({
                "ability_id": ability["id"], "hero_id": hid, "internal_name": ability.get("name"),
                "display_name": ability.get("name_loc"), "description": clean_text(ability.get("desc_loc")),
                "notes": ability.get("notes_loc") or [], "is_innate": bool(ability.get("ability_is_innate")),
                "is_ultimate": ability.get("type") == 1,
                "damage_type": {1: "Physical", 2: "Magical", 4: "Pure"}.get(ability.get("damage")),
                "damage_type_code": ability.get("damage"),
                "behavior": ability.get("behavior"), "behavior_code": ability.get("behavior"),
                "target_type": ability.get("target_type"), "target_team": ability.get("target_team"),
                "target_type_code": ability.get("target_type"),
                "target_team_code": ability.get("target_team"), "immunity_code": ability.get("immunity"),
                "dispellable_code": ability.get("dispellable"),
                "pierces_debuff_immunity": True if ability.get("immunity") == 3 or "pierces debuff immunity" in (ability.get("desc_loc") or "").casefold() else None,
                "dispel_type": "Strong" if "strong dispel" in (ability.get("desc_loc") or "").casefold() else "Basic" if "basic dispel" in (ability.get("desc_loc") or "").casefold() else None,
                "cast_range": ability.get("cast_ranges") or [],
                "cast_point": ability.get("cast_points") or [], "duration": ability.get("durations") or [],
                "cooldown": ability.get("cooldowns") or [], "mana_cost": ability.get("mana_costs") or [],
                "health_cost": ability.get("health_costs") or [], "damage": ability.get("damages") or [],
                "radius": [special for special in ability.get("special_values") or [] if "radius" in (special.get("name") or "").casefold()],
                "special_values": ability.get("special_values") or [],
                "scepter_description": clean_text(ability.get("scepter_loc")),
                "shard_description": clean_text(ability.get("shard_loc")), "raw": ability,
            })
    for item in _entities("items", "items"):
        if not item.get("name_loc"):
            continue
        tier = item.get("item_neutral_tier")
        neutral = isinstance(tier, int) and 0 < tier < 100
        active_effect, passive_effect = _item_effects(item.get("desc_loc"))
        item_rows.append({
            "item_id": item["id"], "internal_name": item.get("name"), "display_name": item.get("name_loc"),
            "cost": item.get("item_cost"), "description": clean_text(item.get("desc_loc")),
            "bonuses": [special for special in item.get("special_values") or [] if (special.get("name") or "").startswith("bonus_")],
            "active_effect": active_effect, "passive_effect": passive_effect,
            "cooldown": item.get("cooldowns") or [], "mana_cost": item.get("mana_costs") or [],
            "target_type": item.get("target_type"), "target_type_code": item.get("target_type"),
            "damage_type": {1: "Physical", 2: "Magical", 4: "Pure"}.get(item.get("damage")),
            "damage_type_code": item.get("damage"),
            "dispel_type": "Strong" if "strong dispel" in (item.get("desc_loc") or "").casefold() else "Basic" if "basic dispel" in (item.get("desc_loc") or "").casefold() else None,
            "dispellable_code": item.get("dispellable"), "special_values": item.get("special_values") or [],
            "neutral_tier": tier if neutral else None, "is_neutral": neutral,
            "is_purchasable": (item.get("item_cost") or 0) > 0,
            "raw": item,
        })
    return hero_rows, ability_rows, item_rows
