"""Merge observations, write current Parquet snapshot, and validate coverage."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict

import pyarrow as pa
import pyarrow.parquet as pq

from . import opendota, valve
from .common import FINAL, RAW, REPORTS, read_json, write_json
from .features import extract
from .mapping import Mapper

S = pa.string()
I = pa.int64()
F = pa.float64()
B = pa.bool_()

SCHEMAS = {
    "heroes": {"hero_id": I, "internal_name": S, "display_name": S, "primary_attribute": S,
               **{x: F for x in ("strength", "strength_gain", "agility", "agility_gain", "intelligence", "intelligence_gain", "health", "health_regen", "mana", "mana_regen", "armor", "magic_resistance", "movement_speed", "attack_range", "attack_rate", "damage_min", "damage_max")},
               "attack_type": S, "complexity": I, "facets": S, "role_levels": S, "raw": S},
    "abilities": {"ability_id": I, "hero_id": I, "internal_name": S, "display_name": S,
                  "description": S, "notes": S, "is_innate": B, "is_ultimate": B,
                  "damage_type": S, "behavior": S, "target_type": I, "target_team": I,
                  "pierces_debuff_immunity": B, "dispel_type": S,
                  **{x: I for x in ("damage_type_code", "immunity_code", "dispellable_code")},
                  **{x: S for x in ("cast_range", "cast_point", "radius", "duration", "cooldown", "mana_cost", "health_cost", "damage", "special_values", "scepter_description", "shard_description", "raw")}},
    "items": {"item_id": I, "internal_name": S, "display_name": S, "cost": I, "description": S,
              "bonuses": S, "active_effect": S, "passive_effect": S,
              "cooldown": S, "mana_cost": S, "target_type": I,
              "damage_type": S, "damage_type_code": I, "dispel_type": S,
              "dispellable_code": I, "special_values": S, "neutral_tier": I,
              "is_neutral": B, "is_purchasable": B, "raw": S},
    "hero_features": {"hero_id": I, "feature": S, "value": F, "confidence": F, "derivation_method": S, "evidence": S},
    "matchups": {"source": S, "hero_id": I, "opponent_hero_id": I, "matches": I, "wins": I,
                 "win_rate": F, "normalized_advantage": F, "retrieved_at": S},
    "hero_items": {"source": S, "hero_id": I, "item_id": I, "phase": S,
                   "usage_count": I, "retrieved_at": S},
    "hero_meta": {"source": S, "hero_id": I, "matches": I, "wins": I, "win_rate": F,
                  "professional_matches": I, "professional_wins": I, "professional_bans": I,
                  "rank_brackets": S,
                  "retrieved_at": S, "raw": S},
}


def _coerce(value, dtype):
    if value is None:
        return None
    if dtype == S and not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write(name, rows, schema_map):
    FINAL.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([pa.field(key, dtype) for key, dtype in schema_map.items()])
    columns = {key: pa.array([_coerce(row.get(key), dtype) for row in rows], type=dtype) for key, dtype in schema_map.items()}
    table = pa.Table.from_pydict(columns, schema=schema)
    pq.write_table(table, FINAL / f"{name}.parquet", compression="zstd")


def _validate(heroes, abilities, items, features, matchups, hero_items):
    ids = {row["hero_id"] for row in heroes}
    item_ids = {row["item_id"] for row in items}
    problems = []
    if len(ids) != len(heroes): problems.append("Duplicate hero_id")
    if len(item_ids) != len(items): problems.append("Duplicate item_id")
    if any(row["hero_id"] not in ids for row in abilities): problems.append("Ability references missing hero")
    if any(row["hero_id"] not in ids or row["opponent_hero_id"] not in ids for row in matchups): problems.append("Matchup references missing hero")
    if any(row["hero_id"] not in ids for row in hero_items): problems.append("Hero item references missing hero")
    if any(row["hero_id"] == row["opponent_hero_id"] for row in matchups): problems.append("Self matchup")
    for row in matchups:
        rate = row.get("win_rate")
        if rate is not None and not 0 <= rate <= 1:
            problems.append(f"Invalid win_rate: {row}")
        if row.get("matches") is not None and row.get("wins") is not None and row["wins"] > row["matches"]:
            problems.append(f"wins > matches: {row}")
    if any(row["item_id"] not in item_ids for row in hero_items): problems.append("Hero item references missing Valve item")
    unique_matchups = {(r["hero_id"], r["opponent_hero_id"]) for r in matchups}
    if len(unique_matchups) != len(matchups): problems.append("Duplicate matchup observation key")
    valve_count = read_json(RAW / "valve" / "manifest.json", {}).get("hero_count")
    if valve_count is not None and valve_count != len(heroes):
        problems.append(f"Valve hero coverage {len(heroes)}/{valve_count}")
    per_hero = defaultdict(list)
    for row in features:
        per_hero[row["hero_id"]].append(row["feature"])
    coverage = {str(row["hero_id"]): {"hero": row["display_name"], "count": len(per_hero[row["hero_id"]]), "features": sorted(per_hero[row["hero_id"]])} for row in heroes}
    matchup_coverage = {}
    for source in sorted({row["source"] for row in matchups}):
        by_hero = defaultdict(set)
        for row in matchups:
            if row["source"] == source:
                by_hero[row["hero_id"]].add(row["opponent_hero_id"])
        matchup_coverage[source] = {"pairs": sum(map(len, by_hero.values())),
                                     "opponents_per_hero": {str(h): len(by_hero[h]) for h in ids},
                                     "missing_opponent_ids": {str(h): sorted(ids - {h} - by_hero[h]) for h in ids if ids - {h} - by_hero[h]}}
    return problems, coverage, matchup_coverage


def build():
    heroes, abilities, items = valve.normalize()
    if not heroes:
        raise RuntimeError("Valve raw snapshot missing: run collect valve first")
    mapper = Mapper(heroes, items)
    meta, open_matchups, open_items = opendota.normalize()
    for row in meta:
        row["hero_id"] = mapper.resolve("hero", numeric_id=row["hero_id"], source="opendota")
    meta = [row for row in meta if row["hero_id"] is not None]
    matchups = []
    for row in open_matchups:
        row["hero_id"] = mapper.resolve("hero", numeric_id=row["hero_id"], source="opendota")
        row["opponent_hero_id"] = mapper.resolve("hero", numeric_id=row["opponent_hero_id"], source="opendota")
        if row["hero_id"] and row["opponent_hero_id"]:
            matchups.append(row)
    hero_items = []
    opendota_item_constants = read_json(RAW / "opendota" / "constants" / "items.json", {})
    opendota_items_by_id = {value.get("id"): (key, value) for key, value in opendota_item_constants.items() if isinstance(value, dict) and value.get("id") is not None}
    for row in open_items:
        row["hero_id"] = mapper.resolve("hero", numeric_id=row["hero_id"], source="opendota")
        constant_name, constant_value = opendota_items_by_id.get(row["item_id"], (None, {}))
        row["item_id"] = mapper.resolve("item", numeric_id=row["item_id"],
                                        internal_name=f"item_{constant_name}" if constant_name else None,
                                        display_name=constant_value.get("dname"), source="opendota")
        if row["hero_id"] and row["item_id"]:
            hero_items.append(row)
    features, matrix = extract(heroes, abilities)
    for name, rows in [
        ("heroes", heroes), ("abilities", abilities), ("items", items),
        ("hero_features", features), ("matchups", matchups),
        ("hero_items", hero_items), ("hero_meta", meta),
    ]:
        _write(name, rows, SCHEMAS[name])
    _write("hero_feature_matrix", matrix, {"hero_id": I, **{key: F for key in sorted({key for row in matrix for key in row if key != "hero_id"})}})
    problems, feature_coverage, matchup_coverage = _validate(heroes, abilities, items, features, matchups, hero_items)
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_json(REPORTS / "ambiguous_mappings.json", mapper.problems)
    write_json(REPORTS / "feature_coverage.json", feature_coverage)
    write_json(REPORTS / "matchup_coverage.json", matchup_coverage)
    write_json(REPORTS / "low_confidence_features.json", [row for row in features if row["confidence"] < .9])
    baseline_prefixes = ("BASE_", "MAX_", "ARMOR_PERCENTILE")
    baseline_suffixes = ("_COUNT",)
    semantic_counts = Counter(row["hero_id"] for row in features if not row["feature"].startswith(baseline_prefixes) and not row["feature"].endswith(baseline_suffixes) and row["feature"] not in ("MOVEMENT_SPEED", "ATTACK_RANGE", "MELEE"))
    with (REPORTS / "feature_review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["hero_id", "hero", "semantic_feature_count", "semantic_features", "manual_features"])
        for hero in sorted(heroes, key=lambda row: row["display_name"]):
            hid = hero["hero_id"]
            semantic = sorted(row["feature"] for row in features if row["hero_id"] == hid and not row["feature"].startswith(baseline_prefixes) and not row["feature"].endswith(baseline_suffixes) and row["feature"] not in ("MOVEMENT_SPEED", "ATTACK_RANGE", "MELEE"))
            manual = sorted(row["feature"] for row in features if row["hero_id"] == hid and row["derivation_method"] == "manual")
            writer.writerow([hid, hero["display_name"], len(semantic), "; ".join(semantic), "; ".join(manual)])
    write_json(REPORTS / "heroes_needing_review.json", [
        {"hero_id": hero["hero_id"], "hero": hero["display_name"], "semantic_feature_count": semantic_counts[hero["hero_id"]]}
        for hero in heroes if semantic_counts[hero["hero_id"]] < 2
    ])
    source_status = {
        "valve": {"raw_snapshot": (RAW / "valve" / "manifest.json").exists(), "hero_rows": len(heroes), "ability_rows": len(abilities), "item_rows": len(items)},
        "opendota": {"raw_snapshot": (RAW / "opendota" / "manifest.json").exists(), "matchup_rows": sum(row["source"] == "opendota" for row in matchups)},
    }
    write_json(REPORTS / "validation.json", {"errors": problems, "source_status": source_status,
                                                "source_manifests": {source: read_json(RAW / source / "manifest.json", {}) for source in ("valve", "opendota")}})
    unavailable = []
    if source_status["opendota"]["raw_snapshot"]:
        unavailable.append({"source": "opendota", "data": ["overall pick/ban rates", "item usage rates"],
                            "reason": "Public aggregate response lacks the required denominators"})
        missing_pairs = sum(len(v) for v in matchup_coverage.get("opendota", {}).get("missing_opponent_ids", {}).values())
        if missing_pairs:
            unavailable.append({"source": "opendota", "data": [f"{missing_pairs} directional matchup pairs"],
                                "reason": "Not present in source responses; see matchup_coverage.json"})
    write_json(REPORTS / "unavailable_data.json", unavailable)
    counts = Counter(row["hero_id"] for row in features)
    summary = {
        "heroes": len(heroes), "abilities": len(abilities), "items": len(items),
        "unique_features": len({row["feature"] for row in features}),
        "hero_feature_relations": len(features), "mean_features_per_hero": round(len(features) / len(heroes), 2),
        "min_features_per_hero": min(counts.values(), default=0), "max_features_per_hero": max(counts.values(), default=0),
        "matchups_by_source": dict(Counter(row["source"] for row in matchups)),
        "hero_items": len(hero_items),
        "mapping_problems": len(mapper.problems), "validation_errors": len(problems),
    }
    write_json(REPORTS / "summary.json", summary)
    return summary
