"""Load reviewed, source-backed gameplay facts as ordinary ontology effects."""
from __future__ import annotations

import html
import json
import re

from rdflib import Graph, RDF, RDFS, URIRef

from .ontology import entity, literal
from .ontology_schema import (KG, RES, DISPEL_CODES, DISPELLABLE_DEBUFFS,
                              DISPELLABLE_BUFFS, bind)

SCOPES = {"self", "ally", "enemy", "area", "both"}
CATEGORIES = {"control", "damage", "defense", "recovery", "support", "mobility",
              "vision", "resource", "summons", "interaction"}
FIELDS = {"description", "notes", "scepter_description", "shard_description", "bonuses",
          "damage_type", "dispel_type", "pierces_debuff_immunity"}
FACT_FIELDS = {"entity_kind", "entity_id", "mechanic", "target_scope", "variant",
               "requirements", "detail_ru", "evidence", "evidence_field"}


def normalized(value):
    """Normalize display markup, preserving the wording of gameplay evidence."""
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)
    if isinstance(value, bool):
        value = str(value).lower()
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def checked_mechanics(rows):
    """Reject stale evidence, broken references and unqualified upgrade facts."""
    catalog = {}
    for row in rows.get("mechanic_catalog", []):
        if (set(row) != {"mechanic", "label_ru", "category"}
                or not re.fullmatch(r"[A-Z][A-Z0-9_]*", row["mechanic"])
                or not row["label_ru"] or row["category"] not in CATEGORIES
                or row["mechanic"] in catalog):
            raise ValueError("Invalid mechanic catalog entry")
        catalog[row["mechanic"]] = row
    owners = {(kind, row[id_key]): row
              for table, kind, id_key in (("abilities", "ability", "ability_id"), ("items", "item", "item_id"))
              for row in rows.get(table, [])}
    items = {row["item_id"]: row for row in rows.get("items", [])}
    keys, result = set(), []
    for fact in rows.get("gameplay_mechanics", []):
        if (set(fact) != FACT_FIELDS or fact["entity_kind"] not in {"ability", "item"}
                or type(fact["entity_id"]) is not int
                or fact["mechanic"] not in catalog or fact["target_scope"] not in SCOPES
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", fact["variant"])
                or fact["evidence_field"] not in FIELDS
                or not normalized(fact["detail_ru"]) or not normalized(fact["evidence"])):
            raise ValueError("Invalid gameplay mechanic fact")
        key = tuple(fact[k] for k in ("entity_kind", "entity_id", "mechanic", "target_scope", "variant"))
        if key in keys:
            raise ValueError("Duplicate gameplay mechanic fact")
        keys.add(key)
        owner = owners.get(key[:2])
        if owner is None:
            raise ValueError("Gameplay mechanic references an unknown entity")
        field = fact["evidence_field"]
        source = owner.get(field)
        if field in {"notes", "bonuses"}:
            source = json.loads(source or "[]")
        if field == "bonuses":
            supported = any(normalized(fact["evidence"]) == normalized(json.dumps(p, ensure_ascii=False)) for p in source)
        else:
            supported = normalized(fact["evidence"]) in normalized(source)
        if not supported:
            raise ValueError(f"Gameplay evidence needs review: {key}/{field}")
        conditions = json.loads(fact["requirements"])
        if not isinstance(conditions, list):
            raise ValueError("Gameplay requirements must be an array")
        codes, required_items = set(), set()
        for condition in conditions:
            if (not isinstance(condition, dict)
                    or not {"kind", "code", "scope", "text"} <= condition.keys()
                    or condition.keys() - {"kind", "code", "scope", "text", "item_id"}
                    or condition["kind"] not in {"has-item", "gameplay"}
                    or condition["scope"] not in {"source", "target"}
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", condition["code"])
                    or not condition["text"] or condition["code"] in codes):
                raise ValueError("Invalid gameplay condition")
            codes.add(condition["code"])
            if condition["kind"] == "has-item":
                item = items.get(condition.get("item_id"))
                if item is None or item["internal_name"] != condition["code"]:
                    raise ValueError("Gameplay condition references an unknown item")
                if condition["scope"] == "source":
                    required_items.add(item["internal_name"])
            elif "item_id" in condition:
                raise ValueError("A gameplay state is not an item requirement")
        raw = json.loads(owner.get("raw") or "{}")
        for upgrade, item_name in (("scepter", "item_ultimate_scepter"), ("shard", "item_aghanims_shard")):
            if ((field == upgrade + "_description" or raw.get("ability_is_granted_by_" + upgrade))
                    and item_name not in required_items):
                raise ValueError("Upgrade mechanic is missing its item requirement")
        result.append((fact, conditions))
    return catalog, result


def build_gameplay_mechanics(game, rows, legacy=None):
    """Reuse Mechanic, Effect and InteractionCondition; introduce no new classes."""
    catalog, facts = checked_mechanics(rows)
    graph = bind(Graph())
    for code, definition in catalog.items():
        mechanic = RES[f"mechanic/{code}"]
        graph.add((mechanic, RDF.type, KG.Mechanic))
        # Preserve labels of existing mechanics referenced by counter rules.
        labels = list(legacy.objects(mechanic, RDFS.label)) if legacy is not None else []
        labels = [literal(definition["label_ru"]) if str(label) == code else label for label in labels]
        labels = labels or [literal(definition["label_ru"])]
        for label in labels:
            graph.add((mechanic, RDFS.label, label))
        graph.add((mechanic, KG.mechanicCategory, literal(definition["category"])))
    for fact, conditions in facts:
        owner = entity(fact["entity_kind"], fact["entity_id"])
        if not list(game.objects(owner, RDF.type)):
            raise ValueError("Gameplay effect owner is absent from the graph")
        effect = URIRef(f"{owner}/effect/{fact['mechanic']}/{fact['target_scope']}/{fact['variant']}")
        graph.add((owner, KG.hasEffect, effect))
        graph.add((effect, RDF.type, KG.Effect))
        title = str(game.value(owner, RDFS.label)) + ": " + catalog[fact["mechanic"]]["label_ru"]
        graph.add((effect, RDFS.label, literal(title)))
        graph.add((effect, KG.effectMechanic, RES[f"mechanic/{fact['mechanic']}"]))
        graph.add((effect, KG.effectTarget, literal(fact["target_scope"])))
        graph.add((effect, KG.evidence, literal(fact["evidence"])))
        graph.add((effect, RDFS.comment, literal(fact["detail_ru"])))
        code_value = game.value(owner, KG.dispellableCode)
        if (fact['mechanic'] in DISPELLABLE_DEBUFFS or fact['mechanic'] in DISPELLABLE_BUFFS) and code_value is not None:
            required = DISPEL_CODES.get(code_value.toPython())
            if required is not None:
                graph.add((effect, KG.requiredDispel, literal(required)))
        # Preserve gameplay restrictions from coarse facts, such as melee cleave
        # and positioning, without carrying technical review-status nodes.
        if legacy is not None:
            mechanic = RES[f"mechanic/{fact['mechanic']}"]
            for coarse in legacy.objects(owner, KG.hasEffect):
                if legacy.value(coarse, KG.effectMechanic) != mechanic:
                    continue
                for condition in legacy.objects(coarse, KG.requiresCondition):
                    if legacy.value(condition, KG.conditionKind) in {literal("has-mechanic"), literal("gameplay")}:
                        graph.add((effect, KG.requiresCondition, condition))
        for requirement in conditions:
            condition = URIRef(f"{effect}/condition/{requirement['code']}")
            graph.add((effect, KG.requiresCondition, condition))
            graph.add((condition, RDF.type, KG.InteractionCondition))
            graph.add((condition, RDFS.label, literal(requirement["text"])))
            for key, prop in (("kind", KG.conditionKind), ("scope", KG.conditionScope), ("text", KG.conditionText)):
                graph.add((condition, prop, literal(requirement[key])))
            if requirement["kind"] == "has-item":
                graph.add((condition, KG.conditionItem, entity("item", requirement["item_id"])))
    return graph
