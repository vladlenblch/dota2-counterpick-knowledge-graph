"""Readable game entities, parameters and observations, built from source tables."""
from __future__ import annotations

import json
import re

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from .common import clean_text
from .ontology import entity, literal, record_uri
from .ontology_schema import KG, ROLE_PROPERTIES, JSON_COLUMNS, NON_DOMAIN_COLUMNS, IDENTITY_COLUMNS, FEATURE_LABELS, NUMERIC_FEATURES, column_predicate, bind, evidence_text


NATIVE_PARAMETERS = {
    "cast_range": (KG.hasCastRange, "Дальность применения", ("AbilityCastRange", "cast_range")),
    "cast_point": (KG.hasCastPoint, "Задержка применения", ("AbilityCastPoint", "cast_point")),
    "channel_time": (KG.hasChannelTime, "Время поддержания", ("AbilityChannelTime", "channel_time")),
    "duration": (KG.hasDuration, "Длительность", ("AbilityDuration", "duration")),
    "cooldown": (KG.hasCooldown, "Перезарядка", ("AbilityCooldown", "cooldown")),
    "mana_cost": (KG.hasManaCost, "Расход маны", ("AbilityManaCost", "mana_cost")),
    "health_cost": (KG.hasHealthCost, "Расход здоровья", ("AbilityHealthCost", "health_cost")),
    "damage": (KG.hasDamage, "Урон", ("AbilityDamage", "damage")),
}
SERIES = {"values_float": KG.hasBaseValue, "values_shard": KG.hasShardValue,
          "values_scepter": KG.hasScepterValue}
SERIES_TITLES = {KG.hasBaseValue: "значение", KG.hasShardValue: "Shard", KG.hasScepterValue: "Scepter"}
OBSERVATIONS = {
    "hero_meta": KG.HeroStatisticsObservation, "matchups": KG.MatchupObservation,
    "hero_items": KG.ItemUsageObservation,
}


def default_zero(values):
    """Recognize generic API defaults without discarding explicit float zeroes."""
    return all(type(value) is int and value == 0 for value in values)


def damage_parameter(parameter):
    """Find damage components by Valve's tooltip heading, without summing them.

    Headings distinguish damage amounts/factors from protection, timing and
    area parameters. An unlabelled value is used only for the native aliases;
    other ambiguous fields remain accessible through hasParameter.
    """
    heading = clean_text(parameter["heading_loc"]) or ""
    if not heading:
        return parameter["name"] in {"AbilityDamage", "damage"}
    words = set(re.findall(r"[A-Z]+", heading.upper()))
    excluded = {"REDUCTION", "REDUCED", "RESISTANCE", "BLOCK", "BLOCKED", "BARRIER",
                "ABSORB", "ABSORBED", "TAKEN", "INCOMING", "THRESHOLD", "DURATION",
                "INTERVAL", "COOLDOWN", "RADIUS", "RANGE", "AOE", "DELAY", "DRAIN", "RATE",
                "AMP", "AMPLIFICATION", "INCREASED", "COST"}
    if not words & {"DAMAGE", "DMG", "DPS"} or words & excluded:
        return False
    if "TO HEALING" in heading.upper() or parameter["name"] == "minimum_damage":
        return False  # Healing conversion and a minimum triggering threshold.
    # A tick interval has a damage heading too, whereas damage PER TICK is an amount.
    return "TICK" not in words or "PER" in words


def channel_plan(field, source, values, *, retain_zero=False):
    """Select named components and, when informative, a separate native series.

    Named values, units, modifiers and order stay unchanged. A generic zero
    must not hide a named effect duration or an ongoing resource cost. Zero
    cast points, mana costs and cooldowns without alternatives are retained.
    """
    from .ontology_validation import same_json

    if any(type(v) not in (int, float) for v in values):
        raise ValueError("Nonnumeric gameplay channel value")
    _, _, aliases = NATIVE_PARAMETERS[field]
    indices = []
    for index, parameter in enumerate(source):
        name = parameter["name"]
        heading = (clean_text(parameter["heading_loc"]) or "").upper()
        words = set(re.findall(r"[A-Z]+", heading))
        reduction = bool(words & {"REDUCTION", "REDUCED", "AMPLIFICATION", "AMP"})
        selected = name in aliases
        if field == "damage":
            selected = damage_parameter(parameter)
        elif field == "duration":
            selected |= name.endswith("_duration") and not parameter["is_percentage"]
            selected |= "DURATION" in words and not reduction and not parameter["is_percentage"]
        elif field == "channel_time":
            selected |= name.endswith("_channel_time") or {"CHANNEL", "TIME"} <= words and not reduction
        elif field in {"mana_cost", "health_cost"}:
            resource = {"MANA"} if field == "mana_cost" else {"HEALTH", "HP"}
            aliases_in_name = ("mana_cost", "manacost") if field == "mana_cost" else ("health_cost", "healthcost", "hp_cost")
            selected |= (bool(words & resource) and "COST" in words or any(alias in name for alias in aliases_in_name)) and not reduction and "reduction" not in name
            selected |= field == "mana_cost" and heading.rstrip(":") == "MANA/SEC"
        elif field == "cooldown":
            selected |= "COOLDOWN" in words and not reduction and not parameter["is_percentage"]
        elif field == "cast_range" and name == "cast_range":
            # The same name denotes bonuses on Arcane Supremacy, Burrow and
            # Telescope. A labelled alias must agree with a nonzero native
            # distance and must not be a bonus.
            selected = not heading or (bool(values) and not default_zero(values)
                                      and not words & {"BONUS", "REDUCTION"}
                                      and not parameter["is_percentage"]
                                      and same_json(values, parameter["values_float"]))
        if selected:
            indices.append(index)
    keep = bool(values)
    if default_zero(values):
        alternate = any(any(value != 0 for value in source[i]["values_float"]) for i in indices)
        keep &= retain_zero and field not in {"damage", "duration", "channel_time"} and not alternate
    if field == "cast_range":
        for i in indices:
            original = source[i]["values_float"]
            expanded = original * len(values) if len(original) == 1 else original
            if (original and any(type(v) is int and v < 0 for v in original)
                    and len(expanded) == len(values)
                    and all(type(a) is int and type(b) is int and a == b % 2**32
                            for a, b in zip(values, expanded))):
                keep = False  # Keep the explicit signed parameter, not its unsigned API encoding.
    native = None
    if keep:
        matches = [i for i in indices if source[i]["name"] in aliases
                   and same_json(values, source[i]["values_float"])]
        native = matches[0] if matches else field
    return indices, native


def parameter_uri(table, row, index):
    if table == "talents":
        return URIRef(str(row["talent_uri"]) + f"/parameter/{index}")
    kind, key = ("ability", "ability_id") if table == "abilities" else ("item", "item_id")
    return URIRef(str(entity(kind, row[key])) + f"/parameter/{index}")


def checked_json(value, expected_type, context):
    if value is None:
        raise ValueError(f"Unknown structured value needs an explicit mapping: {context}")
    decoded = json.loads(value)
    if type(decoded) is not expected_type:
        raise ValueError(f"Unexpected structured value in {context}")
    return decoded


def checked_parameter(value):
    required = {"name", "values_float", "is_percentage", "heading_loc", "bonuses",
                "values_shard", "values_scepter", "required_facet"}
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - {"facet_bonus"}:
        raise ValueError("Unmapped parameter fields; export stopped to prevent data loss")
    for name in ("name", "heading_loc", "required_facet"):
        if type(value[name]) is not str:
            raise ValueError(f"Invalid parameter {name}")
    if type(value["is_percentage"]) is not bool:
        raise ValueError("Invalid percentage flag")
    for name in (*SERIES, "bonuses"):
        if type(value[name]) is not list:
            raise ValueError(f"Invalid parameter array: {name}")
    return value


def informative_parameter(value):
    """Default empty facet modifiers carry no effect; retain all nonempty data."""
    result = dict(value)
    if (result.get("facet_bonus") == {"name": "", "values": [], "operation": 0}
            and type(result["facet_bonus"]["operation"]) is int):
        result.pop("facet_bonus")
    return result


class DomainBuilder:
    def __init__(self, rows):
        self.rows = rows
        self.groups = {name: bind(Graph()) for name in ("entities", "features", "statistics")}
        self.hero_names = {r["hero_id"]: r["display_name"] for r in rows.get("heroes", [])}
        self.item_names = {r["item_id"]: r["display_name"] for r in rows.get("items", [])}
        self.features = {(r["hero_id"], r["feature"]): r
                         for r in rows.get("hero_features", [])}

    @staticmethod
    def node(graph, node, kind, label):
        graph.add((node, RDF.type, kind))
        graph.add((node, RDFS.label, literal(label)))

    @staticmethod
    def scalars(graph, node, table, row):
        if table == "hero_feature_matrix":
            return
        for name, value in row.items():
            prop = column_predicate(table, name)
            if (value is not None and name not in JSON_COLUMNS.get(table, set())
                    and name not in NON_DOMAIN_COLUMNS | IDENTITY_COLUMNS):
                graph.add((node, prop, literal(evidence_text(value) if name == "evidence" else value)))

    def series(self, graph, parent, predicate, values, title):
        if type(values) is not list:
            raise ValueError("A parameter series must be an array")
        for index, value in enumerate(values):
            if type(value) not in (int, float):
                raise ValueError("Unmapped nonnumeric parameter value")
            node = URIRef(str(parent) + f"/{str(predicate).rsplit('#', 1)[-1]}/{index}")
            self.node(graph, node, KG.ParameterValue, f"{title}: {SERIES_TITLES[predicate]} {index + 1} = {value}")
            graph.add((parent, predicate, node))
            graph.add((node, KG.position, literal(index)))
            graph.add((node, KG.numericValue, literal(value)))

    def parameter(self, graph, owner, table, row, index, original, title):
        p = checked_parameter(original)
        node = parameter_uri(table, row, index)
        label = f"{title}: {p['name']}"
        self.node(graph, node, KG.Parameter, label)
        graph.add((owner, KG.hasParameter, node))
        graph.add((node, KG.parameterName, literal(p["name"])))
        graph.add((node, KG.isPercentage, literal(p["is_percentage"])))
        for name, prop in (("heading_loc", KG.parameterHeading), ("required_facet", KG.requiredFacet)):
            if p[name]:
                graph.add((node, prop, literal(p[name])))
        for name, prop in SERIES.items():
            self.series(graph, node, prop, p[name], label)
        for index, bonus in enumerate(p["bonuses"]):
            self.modifier(graph, node, bonus, "bonus", index, label)
        if "facet_bonus" in informative_parameter(p):
            self.modifier(graph, node, p["facet_bonus"], "facet", 0, label)
        return node

    def modifier(self, graph, parent, original, kind, index, title):
        expected = {"name", "operation", "value" if kind == "bonus" else "values"}
        if type(original) is not dict or original.keys() != expected:
            raise ValueError("Unmapped modifier fields")
        if type(original["name"]) is not str or type(original["operation"]) is not int:
            raise ValueError("Invalid modifier condition or operation")
        node = URIRef(str(parent) + f"/modifier/{kind}/{index}")
        self.node(graph, node, KG.ParameterModifier, f"{title}: {kind} {original['name']}")
        graph.add((parent, KG.hasModifier, node))
        graph.add((node, KG.modifierKind, literal(kind)))
        graph.add((node, KG.position, literal(index)))
        graph.add((node, KG.conditionName, literal(original["name"])))
        graph.add((node, KG.operation, literal(original["operation"])))
        if kind == "bonus":
            if type(original["value"]) not in (int, float):
                raise ValueError("Invalid modifier value")
            graph.add((node, KG.numericValue, literal(original["value"])))
        else:
            self.series(graph, node, KG.hasBaseValue, original["values"], title + ": facet")

    def parameters(self, graph, owner, table, row, title):
        source = checked_json(row["special_values"], list, f"{table}/special_values")
        nodes = [self.parameter(graph, owner, table, row, i, p, title) for i, p in enumerate(source)]
        for field in NATIVE_PARAMETERS:
            values = checked_json(row[field], list, f"{table}/{field}") if field in row else []
            self.channel(graph, owner, source, field, values, title, retain_zero=True)
        for field, predicate in (("radius", KG.hasRadiusParameter), ("bonuses", KG.hasBonusParameter)):
            if field not in row:
                continue
            for selected in checked_json(row[field], list, f"{table}/{field}"):
                from .ontology_validation import same_json
                matches = [i for i, p in enumerate(source) if same_json(p, selected)]
                if not matches:
                    raise ValueError(f"Unmapped {field} outside special_values")
                for index in matches:
                    graph.add((owner, predicate, nodes[index]))

    def channel(self, graph, owner, source, field, values, title, *, retain_zero=False):
        predicate, caption, _ = NATIVE_PARAMETERS[field]
        indices, native = channel_plan(field, source, values, retain_zero=retain_zero)
        for index in indices:
            graph.add((owner, predicate, URIRef(str(owner) + f"/parameter/{index}")))
        if native is None:
            return
        node = URIRef(str(owner) + f"/parameter/{native}")
        if type(native) is str:
            label = f"{title}: {caption}"
            self.node(graph, node, KG.Parameter, label)
            graph.add((node, KG.parameterName, literal(field)))
            graph.add((owner, KG.hasParameter, node))
            self.series(graph, node, KG.hasBaseValue, values, label)
        graph.add((owner, predicate, node))

    def entities(self):
        graph = self.groups["entities"]
        feature_graph = self.groups["features"]
        codes = {r["feature"] for r in self.rows.get("hero_features", [])}
        codes.update(code for row in self.rows.get("hero_feature_matrix", []) for code in row if code != "hero_id")
        for code in sorted(codes):
            node = entity("feature", code)
            kind = "count" if code.endswith("_COUNT") else "numeric" if code in NUMERIC_FEATURES else "binary"
            self.node(feature_graph, node, KG.Feature, FEATURE_LABELS.get(code, code))
            feature_graph.set((node, RDFS.label, Literal(FEATURE_LABELS.get(code, code), lang="ru")))
            feature_graph.add((node, KG.identifier, literal(code)))
            feature_graph.add((node, KG.valueKind, literal(kind)))
            units = {"ARMOR_PERCENTILE": "ratio", "BASE_MAGIC_RESISTANCE": "percent",
                     "BASE_ARMOR": "armor", "MOVEMENT_SPEED": "game_distance_per_second",
                     "ATTACK_RANGE": "game_distance", "MAX_AOE_RADIUS": "game_distance",
                     "MAX_CONTROL_DURATION": "seconds"}
            feature_graph.add((node, KG.unit, literal(units.get(code, "count" if kind == "count" else "indicator"))))
        for table, kind, key, cls in (("heroes", "hero", "hero_id", KG.Hero),
                                      ("abilities", "ability", "ability_id", KG.Ability),
                                      ("items", "item", "item_id", KG.Item)):
            for row in self.rows.get(table, []):
                owner, title = entity(kind, row[key]), row["display_name"] or row["internal_name"]
                self.node(graph, owner, cls, title)
                graph.add((owner, KG.identifier, literal(row[key])))
                self.scalars(graph, owner, table, row)
                if table == "heroes":
                    facets = checked_json(row["facets"], list, "hero/facets")
                    if facets:
                        raise ValueError("New facet records require a domain mapping before export")
                    levels = checked_json(row["role_levels"], list, "hero/roles")
                    if len(levels) > len(ROLE_PROPERTIES):
                        raise ValueError("Unknown hero role needs an explicit property mapping")
                    for prop, level in zip(ROLE_PROPERTIES, levels):
                        if type(level) is not int or not 0 <= level <= 3:
                            raise ValueError("Invalid role level")
                        graph.add((owner, prop, literal(level)))
                else:
                    self.parameters(graph, owner, table, row, title)
                if table == "abilities":
                    hero = entity("hero", row["hero_id"])
                    graph.add((hero, KG.hasAbility, owner))
                    graph.add((owner, KG.hero, hero))
                    for index, text in enumerate(checked_json(row["notes"], list, "ability/notes")):
                        if type(text) is not str:
                            raise ValueError("Invalid ability note")
                        note = URIRef(str(owner) + f"/note/{index}")
                        self.node(graph, note, KG.GameNote, f"{title}: примечание {index + 1}")
                        graph.add((owner, KG.hasNote, note))
                        graph.add((note, KG.position, literal(index)))
                        graph.add((note, KG.noteText, literal(text)))
                        graph.add((note, KG.noteKind, literal("note")))

    def observations(self):
        for table, cls in OBSERVATIONS.items():
            graph = self.groups["statistics"]
            for row in self.rows.get(table, []):
                record, hero = record_uri(table, row), entity("hero", row["hero_id"])
                name = self.hero_names[row["hero_id"]]
                if table == "matchups":
                    title = f"{name} против {self.hero_names[row['opponent_hero_id']]}"
                elif table == "hero_items":
                    title = f"{name}: {self.item_names[row['item_id']]} ({row['phase']})"
                else:
                    title = f"{name}: общая статистика"
                self.node(graph, record, cls, title)
                self.scalars(graph, record, table, row)
                graph.add((record, KG.hero, hero))
                if table == "hero_items":
                    item = entity("item", row["item_id"])
                    graph.add((record, KG.item, item))
                    graph.add((hero, KG.hasItemUsage, record))
                    graph.add((item, KG.usedIn, record))
                elif table == "matchups":
                    graph.add((record, KG.opponent, entity("hero", row["opponent_hero_id"])))
                    graph.add((hero, KG.hasMatchup, record))
                    if row["normalized_advantage"] is not None:
                        baseline = record_uri("hero_meta", {"hero_id": row["hero_id"]})
                        graph.add((record, KG.baselineStatistics, baseline))
                else:
                    graph.add((hero, KG.hasStatistics, record))

    def feature_values(self):
        graph = self.groups["features"]
        for row in self.rows.get("hero_feature_matrix", []):
            hero = entity("hero", row["hero_id"])
            name = self.hero_names[row["hero_id"]]
            for code, value in ((k, v) for k, v in row.items() if k != "hero_id"):
                # Stable addresses keep existing references usable without a vector object.
                node = URIRef(str(record_uri("hero_feature_matrix", row)) + "/value/" + code)
                self.node(graph, node, KG.FeatureValue, f"{name}: {code} = {value}")
                graph.add((hero, KG.hasFeatureValue, node))
                graph.add((node, KG.hero, hero))
                graph.add((node, KG.feature, entity("feature", code)))
                graph.add((node, KG.value, literal(value)))
                observed = self.features.get((row["hero_id"], code))
                graph.add((node, KG.isObserved, literal(observed is not None)))
                if observed is not None:
                    from .ontology_validation import same_json
                    if not same_json(value, observed["value"]):
                        raise ValueError("Observed feature value differs from its matrix cell")
                    graph.add((node, KG.evidence, literal(evidence_text(observed["evidence"]))))
                    abilities = [a for a in self.rows.get("abilities", []) if a["hero_id"] == row["hero_id"]
                                 and any(observed["evidence"].startswith(f"Valve ability {a['display_name']}{suffix}")
                                         for suffix in (":", " damage code =", " immunity code ="))]
                    if len(abilities) == 1:
                        graph.add((node, KG.supportedByAbility, entity("ability", abilities[0]["ability_id"])))

    def build(self):
        self.entities()
        self.observations()
        self.feature_values()
        from .ontology_game_fields import add_game_fields
        add_game_fields(self)
        return self.groups
