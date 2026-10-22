"""Explicit gameplay fields retained from the original cells of final datasets.

Source bookkeeping, image paths, UI ordering and fictional biographies do not
belong to the game model. Mechanics, talent effects and aggregate game
statistics do. Unknown raw keys stop export rather than disappear silently.
"""
from __future__ import annotations

import json
from collections import defaultdict

from rdflib import RDF, RDFS, URIRef

from .common import clean_text
from .ontology import entity, literal, record_uri
from .ontology_schema import KG
from .ontology_validation import same_json as _same


# (predicate, Russian label, source type). Numeric fields retain exact source
# scalar types; their OWL range is the union of integer and double.
HERO_FIELDS = {
    "turn_rate": (KG.turnRate, "Скорость поворота", "number"),
    "projectile_speed": (KG.projectileSpeed, "Скорость снаряда", "integer"),
    "sight_range_day": (KG.dayVision, "Дальность обзора днем", "integer"),
    "sight_range_night": (KG.nightVision, "Дальность обзора ночью", "integer"),
    "npe_desc_loc": (KG.roleSummary, "Краткая игровая характеристика", "string"),
    "hype_loc": (KG.gameplayDescription, "Описание стиля игры", "text"),
}
ABILITY_FIELDS = {
    "ability_has_scepter": (KG.hasScepterUpgrade, "Есть улучшение Scepter", "boolean"),
    "ability_has_shard": (KG.hasShardUpgrade, "Есть улучшение Shard", "boolean"),
    "ability_is_granted_by_scepter": (KG.grantedByScepter, "Предоставляется Scepter", "boolean"),
    "ability_is_granted_by_shard": (KG.grantedByShard, "Предоставляется Shard", "boolean"),
    "max_level": (KG.maxLevel, "Максимальное число уровней", "integer"),
    "flags": (KG.behaviorFlags, "Флаги игровой механики", "integer"),
}
ITEM_FIELDS = {
    "behavior": (KG.behavior, "Код поведения", "string"),
    "target_team": (KG.targetTeam, "Код команды цели", "integer"),
    "immunity": (KG.immunityCode, "Код взаимодействия с невосприимчивостью", "integer"),
    "flags": (KG.behaviorFlags, "Флаги игровой механики", "integer"),
    "max_level": (KG.maxLevel, "Максимальное число уровней", "integer"),
    "item_initial_charges": (KG.initialCharges, "Начальное число зарядов", "integer"),
    "item_stock_max": (KG.stockLimit, "Максимальный запас в магазине", "integer"),
    "item_stock_time": (KG.stockReplenishTime, "Время восстановления запаса магазина", "integer"),
    "item_quality": (KG.qualityCode, "Код категории предмета", "integer"),
    "shard_loc": (KG.shardDescription, "Описание Shard", "text"),
    "scepter_loc": (KG.scepterDescription, "Описание Scepter", "text"),
}
TALENT_FIELDS = {
    "id": (KG.identifier, "Игровой идентификатор", "integer"),
    "name": (KG.internalName, "Внутреннее имя", "string"),
    "desc_loc": (KG.description, "Описание", "text"),
    "shard_loc": (KG.shardDescription, "Описание Shard", "text"),
    "scepter_loc": (KG.scepterDescription, "Описание Scepter", "text"),
    **ABILITY_FIELDS,
}
STATS_FIELDS = {
    "base_str": (KG.strength, "Сила", "double"),
    "base_agi": (KG.agility, "Ловкость", "double"),
    "base_int": (KG.intelligence, "Интеллект", "double"),
    "str_gain": (KG.strengthGain, "Прирост силы", "double"),
    "agi_gain": (KG.agilityGain, "Прирост ловкости", "double"),
    "int_gain": (KG.intelligenceGain, "Прирост интеллекта", "double"),
    "base_health": (KG.baseHealth, "Базовое здоровье", "integer"),
    "base_health_regen": (KG.baseHealthRegen, "Базовое восстановление здоровья", "number"),
    "base_mana": (KG.baseMana, "Базовая мана", "integer"),
    "base_mana_regen": (KG.baseManaRegen, "Базовое восстановление маны", "number"),
    "base_armor": (KG.baseArmor, "Базовая броня", "number"),
    "base_mr": (KG.magicResistance, "Сопротивление магии", "double"),
    "base_attack_min": (KG.baseDamageMin, "Базовый минимальный урон атаки", "integer"),
    "base_attack_max": (KG.baseDamageMax, "Базовый максимальный урон атаки", "integer"),
    "base_attack_time": (KG.baseAttackTime, "Базовое время атаки", "integer"),
    "attack_point": (KG.attackPoint, "Задержка атаки", "number"),
    "attack_type": (KG.attackType, "Тип атаки", "string"),
    "attack_range": (KG.attackRange, "Дальность атаки", "double"),
    "attack_rate": (KG.attackRate, "Интервал атак", "double"),
    "move_speed": (KG.movementSpeed, "Скорость передвижения", "double"),
    "projectile_speed": (KG.projectileSpeed, "Скорость снаряда", "integer"),
    "turn_rate": (KG.turnRate, "Скорость поворота", "number"),
    "day_vision": (KG.dayVision, "Дальность обзора днем", "integer"),
    "night_vision": (KG.nightVision, "Дальность обзора ночью", "integer"),
    "cm_enabled": (KG.captainsModeEnabled, "Доступен в Captains Mode", "boolean"),
    "turbo_picks": (KG.turboMatches, "Матчи Turbo", "integer"),
    "turbo_wins": (KG.turboWins, "Победы Turbo", "integer"),
    "pub_pick": (KG.publicMatches, "Публичные матчи", "integer"),
    "pub_win": (KG.publicWins, "Публичные победы", "integer"),
    "primary_attr": (KG.primaryAttribute, "Основной атрибут", "attribute"),
}
CHANNELS = {
    "cast_ranges": (KG.hasCastRange, "cast_range", "Дальность применения"),
    "cast_points": (KG.hasCastPoint, "cast_point", "Задержка применения"),
    "channel_times": (KG.hasChannelTime, "channel_time", "Время поддержания"),
    "durations": (KG.hasDuration, "duration", "Длительность"),
    "cooldowns": (KG.hasCooldown, "cooldown", "Перезарядка"),
    "mana_costs": (KG.hasManaCost, "mana_cost", "Расход маны"),
    "health_costs": (KG.hasHealthCost, "health_cost", "Расход здоровья"),
    "damages": (KG.hasDamage, "damage", "Урон"),
}
TRENDS = {
    "pub_pick_trend": (KG.hasPublicMatchTrend, "pub_pick_trend", "Динамика числа публичных матчей"),
    "pub_win_trend": (KG.hasPublicWinTrend, "pub_win_trend", "Динамика публичных побед"),
    "turbo_picks_trend": (KG.hasTurboMatchTrend, "turbo_picks_trend", "Динамика числа матчей Turbo"),
    "turbo_wins_trend": (KG.hasTurboWinTrend, "turbo_wins_trend", "Динамика побед Turbo"),
}

HERO_KNOWN = set(HERO_FIELDS) | {
    "id", "name", "name_loc", "bio_loc", "order_id", "facets", "facet_abilities",
    "str_base", "str_gain", "agi_base", "agi_gain", "int_base", "int_gain", "primary_attr",
    "complexity", "attack_capability", "role_levels", "damage_min", "damage_max", "attack_rate",
    "attack_range", "armor", "magic_resistance", "movement_speed", "max_health", "health_regen",
    "max_mana", "mana_regen", "abilities", "talents",
}
SPELL_KNOWN = set(ABILITY_FIELDS) | set(ITEM_FIELDS) | set(CHANNELS) | {
    "id", "name", "name_loc", "desc_loc", "lore_loc", "notes_loc", "facets_loc", "type",
    "target_team", "target_type", "damage", "immunity", "dispellable", "special_values",
    "is_item", "ability_is_innate", "item_cost", "item_neutral_tier", "gold_costs",
}
STATS_KNOWN = set(STATS_FIELDS) | set(TRENDS) | {
    "id", "name", "localized_name", "img", "icon", "primary_attr", "roles", "legs",
    "pro_pick", "pro_win", "pro_ban", "pub_pick", "pub_win",
} | {f"{rank}_{field}" for rank in range(1, 9) for field in ("pick", "win")}


def _raw(row, known, table):
    raw = json.loads(row.get("raw") or "{}")
    if not isinstance(raw, dict):
        raise ValueError(f"Expected an object in {table}.raw")
    unknown = raw.keys() - known
    if unknown:
        raise ValueError(f"Unmapped gameplay fields in {table}.raw: {sorted(unknown)}")
    # Known exclusions are either already mapped aliases, fictional lore/UI,
    # or API defaults without an effect. Defaults must stay empty/constant;
    # a future nonempty value requires an explicit gameplay mapping.
    defaults = {}
    if table == "heroes":
        defaults = {"facets": [], "facet_abilities": []}
    elif table in {"abilities", "items", "talents"}:
        defaults = {"gold_costs": [], "is_item": table == "items"}
        if table != "items":
            defaults.update(item_cost=0, item_initial_charges=0, item_neutral_tier=4294967295,
                            item_stock_max=0, item_stock_time=0, item_quality=0)
        if table == "items":
            defaults.update(type=0, ability_is_innate=False, ability_has_scepter=False,
                            ability_has_shard=False, ability_is_granted_by_scepter=False,
                            ability_is_granted_by_shard=False)
        if table == "talents":
            defaults.update(type=2, target_team=0, target_type=0, damage=0, immunity=0,
                            dispellable=0, ability_is_innate=False)
    for field, expected in defaults.items():
        if field in raw and not _same(raw[field], expected):
            raise ValueError(f"New nondefault gameplay field needs mapping: {table}.{field}")
    return raw


def _value(value, kind):
    if value is None:
        return None
    if kind == "text":
        if type(value) is not str:
            raise ValueError("Gameplay text must be a string")
        return clean_text(value) or None
    if kind == "attribute":
        aliases = {"str": "strength", "agi": "agility", "int": "intelligence", "all": "universal"}
        if value not in aliases:
            raise ValueError("Unknown primary attribute")
        return aliases[value]
    allowed = {"number": (int, float), "double": (int, float), "integer": (int,),
               "boolean": (bool,), "string": (str,)}[kind]
    if type(value) not in allowed:
        raise ValueError(f"Unexpected gameplay scalar type: {kind}/{type(value).__name__}")
    return float(value) if kind == "double" else value


def _array(raw, key):
    value = raw.get(key, [])
    if type(value) is not list:
        raise ValueError(f"Expected ordered gameplay array: {key}")
    return value


def _talent_uri(hero, index):
    return URIRef(str(hero) + f"/talent/{index}")


def _note_uri(owner, kind, index):
    return URIRef(str(owner) + f"/note/{kind}/{index}")


def _add_scalars(graph, owner, raw, mapping):
    for field, (prop, _, kind) in mapping.items():
        if field in raw:
            value = _value(raw[field], kind)
            if value is not None and value != "":
                graph.add((owner, prop, literal(value)))


def _add_notes(builder, graph, owner, raw, title, *, ordinary):
    for field, kind in (("notes_loc", "note"), ("facets_loc", "facet")):
        if kind == "note" and not ordinary:
            continue
        for index, text in enumerate(_array(raw, field)):
            if type(text) is not str:
                raise ValueError("A game note must preserve string values")
            if not text:
                continue
            node = _note_uri(owner, kind, index)
            builder.node(graph, node, KG.GameNote, f"{title}: {'аспект' if kind == 'facet' else 'примечание'} {index + 1}")
            graph.add((owner, KG.hasNote, node))
            graph.add((node, KG.noteKind, literal(kind)))
            graph.add((node, KG.position, literal(index)))
            graph.add((node, KG.noteText, literal(text)))


def _add_channel(builder, graph, owner, values, channel, title):
    prop, code, caption = channel
    if not values:
        return
    if any(type(v) not in (int, float) for v in values):
        raise ValueError("Nonnumeric gameplay channel value")
    existing = list(graph.objects(owner, prop))
    if existing:
        return  # Existing domain channels are independently checked below.
    if all(type(v) is int and v == 0 for v in values):
        return  # API zero defaults do not describe an additional mechanic.
    aliases = {code, "Ability" + "".join(x.capitalize() for x in code.split("_"))}
    for parameter in graph.objects(owner, KG.hasParameter):
        if str(next(graph.objects(parameter, KG.parameterName), "")) in aliases:
            indexed = sorted((int(next(graph.objects(n, KG.position))), next(graph.objects(n, KG.numericValue)).toPython())
                             for n in graph.objects(parameter, KG.hasBaseValue))
            if _same(values, [value for _, value in indexed]):
                graph.add((owner, prop, parameter))
                return
    parameter = URIRef(str(owner) + "/parameter/" + code)
    builder.node(graph, parameter, KG.Parameter, f"{title}: {caption}")
    graph.add((parameter, KG.parameterName, literal(code)))
    graph.add((owner, KG.hasParameter, parameter))
    graph.add((owner, prop, parameter))
    builder.series(graph, parameter, KG.hasBaseValue, values, f"{title}: {caption}")


def add_game_fields(builder):
    """Add game-only raw fields to the builder's existing named entities."""
    graph = builder.groups["entities"]
    talent_names = defaultdict(list)
    abilities = {(r["hero_id"], r["ability_id"]): json.loads(r.get("raw") or "{}")
                 for r in builder.rows.get("abilities", [])}
    for row in builder.rows.get("heroes", []):
        raw = _raw(row, HERO_KNOWN, "heroes")
        hero, title = entity("hero", row["hero_id"]), row["display_name"]
        _add_scalars(graph, hero, raw, HERO_FIELDS)
        for ability in _array(raw, "abilities"):
            if not _same(ability, abilities.get((row["hero_id"], ability["id"]))):
                raise ValueError("Nested hero abilities differ from the exported ability dataset")
        for index, talent in enumerate(_array(raw, "talents")):
            _raw({"raw": json.dumps(talent)}, SPELL_KNOWN, "talents")
            node = _talent_uri(hero, index)
            label = talent.get("name_loc") or talent["name"]
            builder.node(graph, node, KG.Talent, f"{title}: {label}")
            graph.add((hero, KG.hasTalent, node))
            graph.add((node, KG.hero, hero))
            graph.add((node, KG.position, literal(index)))
            _add_scalars(graph, node, talent, TALENT_FIELDS)
            for p_index, parameter in enumerate(_array(talent, "special_values")):
                builder.parameter(graph, node, "talents", {"talent_uri": node}, p_index, parameter, label)
            for field, channel in CHANNELS.items():
                values = _array(talent, field)
                # A talent is passive. Valve's identical zero-filled spell
                # defaults add no mechanics; retain any actual channel value.
                if any(type(v) is not int or v != 0 for v in values):
                    _add_channel(builder, graph, node, values, channel, label)
            _add_notes(builder, graph, node, talent, label, ordinary=True)
            talent_names[talent["name"]].append(node)
    for table, kind, key, mapping in (("abilities", "ability", "ability_id", ABILITY_FIELDS),
                                      ("items", "item", "item_id", ITEM_FIELDS)):
        for row in builder.rows.get(table, []):
            raw = _raw(row, SPELL_KNOWN, table)
            owner, title = entity(kind, row[key]), row["display_name"] or row["internal_name"]
            _add_scalars(graph, owner, raw, mapping)
            for field, channel in CHANNELS.items():
                _add_channel(builder, graph, owner, _array(raw, field), channel, title)
            _add_notes(builder, graph, owner, raw, title, ordinary=table == "items")
    # Talent names are source-provided conditions. Resolve only known exact
    # names and the same hero; unresolved historical conditions remain strings.
    for modifier in list(graph.subjects(RDF.type, KG.ParameterModifier)):
        condition = next(graph.objects(modifier, KG.conditionName), None)
        for talent in talent_names.get(str(condition), []):
            modifier_owner = str(modifier).split("/parameter/", 1)[0]
            owner = URIRef(modifier_owner)
            heroes = set(graph.objects(owner, KG.hero))
            if set(graph.objects(talent, KG.hero)) & heroes:
                graph.add((modifier, KG.requiresTalent, talent))
    stats = builder.groups["statistics"]
    for row in builder.rows.get("hero_meta", []):
        raw = _raw(row, STATS_KNOWN, "hero_meta")
        owner = record_uri("hero_meta", row)
        title = builder.hero_names[row["hero_id"]]
        _add_scalars(stats, owner, raw, STATS_FIELDS)
        for role in _array(raw, "roles"):
            if type(role) is not str:
                raise ValueError("A hero role must have a string name")
            stats.add((owner, KG.roleName, literal(role)))
        for field, channel in TRENDS.items():
            _add_channel(builder, stats, owner, _array(raw, field), channel, title)


def validate_game_fields(graph, rows):
    """Independently decode all mapped fields and return extra populations."""
    expected, relations = defaultdict(set), defaultdict(set)
    counts = defaultdict(int)

    def one(node, prop):
        values = list(graph.objects(node, prop))
        if len(values) != 1:
            raise ValueError(f"Missing or repeated game field: {node}/{prop}")
        return values[0].toPython()

    def scalar(node, prop, value):
        if not _same(one(node, prop), value):
            raise ValueError(f"Gameplay field changed: {node}/{prop}")
        counts["scalar_values_verified"] += 1

    def node(uri, cls):
        if (uri, RDF.type, cls) not in graph or not list(graph.objects(uri, RDFS.label)):
            raise ValueError(f"Missing named gameplay node: {uri}")
        expected[cls].add(uri)

    def link(subject, prop, target):
        if (subject, prop, target) not in graph:
            raise ValueError(f"Missing game relation: {subject}/{prop}/{target}")
        relations[(subject, prop)].add(target)

    def scalars(owner, raw, mapping):
        for field, (prop, _, kind) in mapping.items():
            if field in raw:
                value = _value(raw[field], kind)
                if value is not None and value != "":
                    scalar(owner, prop, value)

    def series(parent, prop, values):
        indexed = {}
        for value_node in graph.objects(parent, prop):
            index = one(value_node, KG.position)
            if type(index) is not int or index in indexed:
                raise ValueError("Repeated gameplay sequence position")
            indexed[index] = value_node
            node(value_node, KG.ParameterValue)
        if set(indexed) != set(range(len(values))):
            raise ValueError("Gameplay sequence lost values")
        for index, value in enumerate(values):
            scalar(indexed[index], KG.numericValue, value)
        counts["ordered_values_verified"] += len(values)

    def channel(owner, raw, field, definition, *, passive=False):
        values = _array(raw, field)
        if any(type(v) not in (int, float) for v in values):
            raise ValueError("Nonnumeric gameplay channel value")
        if not values or passive and all(type(v) is int and v == 0 for v in values):
            return
        prop, _, _ = definition
        targets = list(graph.objects(owner, prop))
        if not targets and all(type(v) is int and v == 0 for v in values):
            return
        if len(targets) != 1:
            raise ValueError(f"Gameplay channel missing or repeated: {owner}/{field}")
        parameter = targets[0]
        node(parameter, KG.Parameter)
        link(owner, prop, parameter)
        link(owner, KG.hasParameter, parameter)
        series(parameter, KG.hasBaseValue, values)
        counts["channels_verified"] += 1

    def notes(owner, raw, *, ordinary):
        for field, kind in (("notes_loc", "note"), ("facets_loc", "facet")):
            if kind == "note" and not ordinary:
                continue
            for index, text in enumerate(_array(raw, field)):
                if not text:
                    continue
                uri = _note_uri(owner, kind, index)
                node(uri, KG.GameNote)
                link(owner, KG.hasNote, uri)
                scalar(uri, KG.noteKind, kind)
                scalar(uri, KG.position, index)
                scalar(uri, KG.noteText, text)
                counts["notes_verified"] += 1

    talent_names = defaultdict(list)
    for row in rows.get("heroes", []):
        raw = _raw(row, HERO_KNOWN, "heroes")
        hero = entity("hero", row["hero_id"])
        scalars(hero, raw, HERO_FIELDS)
        for index, talent in enumerate(_array(raw, "talents")):
            _raw({"raw": json.dumps(talent)}, SPELL_KNOWN, "talents")
            uri = _talent_uri(hero, index)
            node(uri, KG.Talent)
            link(hero, KG.hasTalent, uri)
            link(uri, KG.hero, hero)
            scalar(uri, KG.position, index)
            scalars(uri, talent, TALENT_FIELDS)
            for p_index, original in enumerate(_array(talent, "special_values")):
                parameter = URIRef(str(uri) + f"/parameter/{p_index}")
                node(parameter, KG.Parameter)
                link(uri, KG.hasParameter, parameter)
                scalar(parameter, KG.parameterName, original["name"])
                scalar(parameter, KG.isPercentage, original["is_percentage"])
                for field, prop in (("heading_loc", KG.parameterHeading), ("required_facet", KG.requiredFacet)):
                    if original[field]:
                        scalar(parameter, prop, original[field])
                for field, prop in (("values_float", KG.hasBaseValue), ("values_shard", KG.hasShardValue),
                                    ("values_scepter", KG.hasScepterValue)):
                    series(parameter, prop, original[field])
                # Talents currently contain only base values. Stop if this
                # changes until the validator covers conditional semantics.
                facet = original.get("facet_bonus")
                if original["bonuses"] or facet not in (None, {"name": "", "values": [], "operation": 0}):
                    raise ValueError("New talent modifiers need explicit validation")
                counts["talent_parameters_verified"] += 1
            for field, definition in CHANNELS.items():
                channel(uri, talent, field, definition, passive=True)
            notes(uri, talent, ordinary=True)
            talent_names[talent["name"]].append(uri)
            counts["talents_verified"] += 1
    for table, kind, key, mapping in (("abilities", "ability", "ability_id", ABILITY_FIELDS),
                                      ("items", "item", "item_id", ITEM_FIELDS)):
        for row in rows.get(table, []):
            raw = _raw(row, SPELL_KNOWN, table)
            owner = entity(kind, row[key])
            scalars(owner, raw, mapping)
            for field, definition in CHANNELS.items():
                channel(owner, raw, field, definition)
            notes(owner, raw, ordinary=table == "items")
    for modifier in graph.subjects(RDF.type, KG.ParameterModifier):
        condition = str(next(graph.objects(modifier, KG.conditionName), ""))
        owner = URIRef(str(modifier).split("/parameter/", 1)[0])
        wanted = {t for t in talent_names.get(condition, [])
                  if set(graph.objects(owner, KG.hero)) & set(graph.objects(t, KG.hero))}
        if set(graph.objects(modifier, KG.requiresTalent)) != wanted:
            raise ValueError("Talent condition linked to a different hero or effect")
        for target in wanted:
            link(modifier, KG.requiresTalent, target)
            counts["talent_effect_links_verified"] += 1
    for row in rows.get("hero_meta", []):
        raw = _raw(row, STATS_KNOWN, "hero_meta")
        owner = record_uri("hero_meta", row)
        scalars(owner, raw, STATS_FIELDS)
        if {str(v) for v in graph.objects(owner, KG.roleName)} != set(_array(raw, "roles")):
            raise ValueError("Hero role names changed")
        for field, definition in TRENDS.items():
            channel(owner, raw, field, definition)
    return {"expected": dict(expected), "relations": dict(relations), "counts": dict(counts)}
