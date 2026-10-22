"""Decode the game model and verify its information against all source tables."""
from __future__ import annotations

import json
import re
from collections import defaultdict

from rdflib import BNode, RDF, RDFS, OWL, URIRef

from .ontology import entity, literal, record_uri
from .ontology_schema import KG, JSON_COLUMNS, NON_DOMAIN_COLUMNS, IDENTITY_COLUMNS, FEATURE_LABELS, NUMERIC_FEATURES, column_predicate, evidence_text
from .ontology_domain import NATIVE_PARAMETERS, OBSERVATIONS, SERIES, parameter_uri, informative_parameter
from .ontology_validation import one, check_scalar, same_json


class DomainReader:
    def __init__(self, graph):
        self.g = graph

    def scalar(self, node, prop, *, optional=False, default=None):
        value = one(self.g, node, prop, optional=optional)
        return value.toPython() if value is not None else default

    def ordered(self, parent, prop, *, exclude=()):
        indexed = {}
        for node in self.g.objects(parent, prop):
            if node in exclude:
                continue
            index = self.scalar(node, KG.position)
            if type(index) is not int or index in indexed:
                raise ValueError("Invalid or duplicate domain position")
            indexed[index] = node
        if set(indexed) != set(range(len(indexed))):
            raise ValueError("Missing domain sequence positions")
        return [indexed[i] for i in range(len(indexed))]

    def series(self, parent, prop):
        values = [self.scalar(n, KG.numericValue) for n in self.ordered(parent, prop)]
        if any(type(v) not in (int, float) for v in values):
            raise ValueError("Parameter values must preserve their numeric types")
        return values

    def parameter(self, node):
        result = {"name": self.scalar(node, KG.parameterName),
                  "is_percentage": self.scalar(node, KG.isPercentage),
                  "heading_loc": self.scalar(node, KG.parameterHeading, optional=True, default=""),
                  "required_facet": self.scalar(node, KG.requiredFacet, optional=True, default=""),
                  **{name: self.series(node, prop) for name, prop in SERIES.items()}}
        by_kind = defaultdict(dict)
        for modifier in self.g.objects(node, KG.hasModifier):
            kind = self.scalar(modifier, KG.modifierKind)
            position = self.scalar(modifier, KG.position)
            if kind not in {"bonus", "facet"} or type(position) is not int or position in by_kind[kind]:
                raise ValueError("Invalid modifier kind or order")
            value = {"name": self.scalar(modifier, KG.conditionName),
                     "operation": self.scalar(modifier, KG.operation)}
            if kind == "bonus":
                value["value"] = self.scalar(modifier, KG.numericValue)
            else:
                value["values"] = self.series(modifier, KG.hasBaseValue)
            by_kind[kind][position] = value
        bonuses = by_kind["bonus"]
        if set(bonuses) != set(range(len(bonuses))):
            raise ValueError("Missing bonus modifier positions")
        result["bonuses"] = [bonuses[i] for i in range(len(bonuses))]
        if by_kind["facet"]:
            if set(by_kind["facet"]) != {0}:
                raise ValueError("Unexpected facet modifier cardinality")
            result["facet_bonus"] = by_kind["facet"][0]
        return result


def validate_game(game, rows):
    """Decode the saved graph independently and compare every informative field."""
    reader = DomainReader(game)
    technical = {"source", "sourceCode", "retrievedAtText", "collectedAt", "snapshot",
                 "sha256", "relativePath", "rawRecord", "method", "facetCount",
                 "hasRecord", "parameterData", "jsonRoot", "hasArtifact", "sourceIndex"}
    technical_predicates = {KG[name] for name in technical}
    if any(p in technical_predicates for _, p, _ in game):
        raise ValueError("Technical fields leaked into the domain ontology")
    for triple in game:
        if any(isinstance(t, URIRef) and re.search(r"[0-9a-f]{64}", str(t)) for t in triple):
            raise ValueError("Hash identifiers leaked into the game model")
        if any(isinstance(t, BNode) for t in triple) and not (
                triple[1] in {RDF.first, RDF.rest, OWL.members}
                or triple[1:] == (RDF.type, OWL.AllDisjointClasses)):
            raise ValueError("Anonymous data nodes leaked into the game model")
    classes = set(game.subjects(RDF.type, OWL.Class))
    for _, _, kind in game.triples((None, RDF.type, None)):
        if str(kind).startswith(str(KG)) and kind not in classes:
            raise ValueError(f"Undeclared domain class: {kind}")
    for subject in {s for s, p, _ in game if str(p).startswith(str(KG))}:
        if not set(game.objects(subject, RDF.type)) & classes:
            raise ValueError(f"Untyped domain data: {subject}")
    properties = set(game.subjects(RDF.type, OWL.ObjectProperty)) | set(game.subjects(RDF.type, OWL.DatatypeProperty))
    undeclared = {p for _, p, _ in game if str(p).startswith(str(KG)) and p not in properties}
    if undeclared:
        raise ValueError(f"Undeclared game properties: {undeclared}")
    from .ontology_game_fields import validate_game_fields
    supplemental = validate_game_fields(game, rows)
    extra_expected = supplemental["expected"]
    extra_relations = supplemental["relations"]
    expected = defaultdict(set)
    relations = defaultdict(set)
    counts = defaultdict(int)
    counts.update(supplemental["counts"])

    def expect(node, cls):
        expected[cls].add(node)
        if (node, RDF.type, cls) not in game or not list(game.objects(node, RDFS.label)):
            raise ValueError(f"Missing game type or label: {node}")

    def link(subject, prop, target):
        if (subject, prop, target) not in game:
            raise ValueError(f"Missing informative relation: {subject}/{prop}/{target}")

    def scalar(node, prop, value):
        actual = one(game, node, prop, optional=value is None)
        if value is None:
            if actual is not None:
                raise ValueError("A missing value was invented")
        else:
            check_scalar(actual, value, literal(value).datatype)

    def original_scalars(node, table, row):
        if table == "hero_feature_matrix":
            return
        for name, value in row.items():
            prop = column_predicate(table, name)
            if name not in JSON_COLUMNS.get(table, set()) and name not in NON_DOMAIN_COLUMNS | IDENTITY_COLUMNS:
                scalar(node, prop, evidence_text(value) if name == "evidence" else value)
                counts["scalar_values_verified"] += 1

    def validate_series(parent, prop, values):
        if not same_json(values, reader.series(parent, prop)):
            raise ValueError(f"Parameter series changed: {parent}/{prop}")
        for node in reader.ordered(parent, prop):
            expect(node, KG.ParameterValue)
        counts["parameter_values_verified"] += len(values)

    def validate_parameters(owner, table, row):
        source = json.loads(row["special_values"])
        wanted = set()
        for index, original in enumerate(source):
            node = parameter_uri(table, row, index)
            wanted.add(node)
            expect(node, KG.Parameter)
            link(owner, KG.hasParameter, node)
            if not same_json(informative_parameter(original), reader.parameter(node)):
                raise ValueError(f"Parameter, modifiers or scalar types changed: {node}")
            for name, prop in SERIES.items():
                validate_series(node, prop, original[name])
            for modifier in game.objects(node, KG.hasModifier):
                expect(modifier, KG.ParameterModifier)
                if reader.scalar(modifier, KG.modifierKind) == "facet":
                    validate_series(modifier, KG.hasBaseValue, reader.series(modifier, KG.hasBaseValue))
            counts["source_parameters_verified"] += 1
            counts["bonus_modifiers_verified"] += len(original["bonuses"])
            counts["facet_modifiers_verified"] += "facet_bonus" in informative_parameter(original)
        for field, (prop, _, names) in NATIVE_PARAMETERS.items():
            if field not in row:
                continue
            values = json.loads(row[field])
            actual = list(game.objects(owner, prop))
            if not values:
                if actual:
                    raise ValueError("An empty native parameter series was invented")
                continue
            if len(actual) != 1:
                raise ValueError("Native parameter link missing or duplicated")
            node = actual[0]
            expect(node, KG.Parameter)
            wanted.add(node)
            if reader.scalar(node, KG.parameterName) not in {*names, field}:
                raise ValueError("Native series linked to an unrelated parameter")
            validate_series(node, KG.hasBaseValue, values)
        wanted |= extra_relations.get((owner, KG.hasParameter), set())
        relations[(owner, KG.hasParameter)].update(wanted)
        if set(game.objects(owner, KG.hasParameter)) != wanted:
            raise ValueError("Owner parameters lost or duplicated")
        for field, prop in (("radius", KG.hasRadiusParameter), ("bonuses", KG.hasBonusParameter)):
            if field in row:
                chosen = json.loads(row[field])
                targets = {parameter_uri(table, row, i) for i, p in enumerate(source)
                           if any(same_json(p, selected) for selected in chosen)}
                if set(game.objects(owner, prop)) != targets:
                    raise ValueError(f"Informative {field} parameter links changed")

    for table, kind, key, cls in (("heroes", "hero", "hero_id", KG.Hero),
                                  ("abilities", "ability", "ability_id", KG.Ability),
                                  ("items", "item", "item_id", KG.Item)):
        for row in rows.get(table, []):
            owner = entity(kind, row[key])
            expect(owner, cls)
            scalar(owner, KG.identifier, row[key])
            scalar(owner, RDFS.label, row["display_name"] or row["internal_name"])
            original_scalars(owner, table, row)
            if table == "heroes":
                facets = json.loads(row["facets"])
                if facets:
                    raise ValueError("Facet data has not been mapped")
                source = json.loads(row["role_levels"])
                nodes = {reader.scalar(n, KG.roleCode): n for n in game.objects(owner, KG.hasRoleRating)}
                if set(nodes) != set(range(len(source))) or len(nodes) != len(list(game.objects(owner, KG.hasRoleRating))):
                    raise ValueError("Role codes changed")
                for code, value in enumerate(source):
                    expect(nodes[code], KG.RoleRating)
                    scalar(nodes[code], KG.roleLevel, value)
                    counts["role_levels_verified"] += 1
            else:
                validate_parameters(owner, table, row)
            if table == "abilities":
                hero = entity("hero", row["hero_id"])
                link(owner, KG.hero, hero)
                link(hero, KG.hasAbility, owner)
                relations[(hero, KG.hasAbility)].add(owner)
                extra_notes = extra_relations.get((owner, KG.hasNote), set())
                notes = reader.ordered(owner, KG.hasNote, exclude=extra_notes)
                relations[(owner, KG.hasNote)].update(notes)
                if not same_json(json.loads(row["notes"]), [reader.scalar(n, KG.noteText) for n in notes]):
                    raise ValueError("Ability notes or their order changed")
                for note in notes:
                    expect(note, KG.GameNote)
                    scalar(note, KG.noteKind, "note")
                counts["notes_verified"] += len(notes)

    features = {(r["hero_id"], r["feature"]): record_uri("hero_features", r)
                for r in rows.get("hero_features", [])}
    for table, cls in OBSERVATIONS.items():
        for row in rows.get(table, []):
            record, hero = record_uri(table, row), entity("hero", row["hero_id"])
            expect(record, cls)
            original_scalars(record, table, row)
            link(record, KG.hero, hero)
            if table == "hero_features":
                feature = entity("feature", row["feature"])
                expect(feature, KG.Feature)
                link(record, KG.feature, feature)
                link(hero, KG.hasFeatureObservation, record)
                relations[(hero, KG.hasFeatureObservation)].add(record)
                abilities = [a for a in rows.get("abilities", []) if a["hero_id"] == row["hero_id"]
                             and any(row["evidence"].startswith(f"Valve ability {a['display_name']}{suffix}")
                                     for suffix in (":", " damage code =", " immunity code ="))]
                targets = {entity("ability", abilities[0]["ability_id"])} if len(abilities) == 1 else set()
                if set(game.objects(record, KG.supportedByAbility)) != targets:
                    raise ValueError("Ability evidence relation changed")
            elif table == "hero_items":
                item = entity("item", row["item_id"])
                link(record, KG.item, item)
                link(hero, KG.hasItemUsage, record)
                link(item, KG.usedIn, record)
                relations[(hero, KG.hasItemUsage)].add(record)
                relations[(item, KG.usedIn)].add(record)
            elif table == "matchups":
                link(record, KG.opponent, entity("hero", row["opponent_hero_id"]))
                link(hero, KG.hasMatchup, record)
                relations[(hero, KG.hasMatchup)].add(record)
                baseline = record_uri("hero_meta", {"hero_id": row["hero_id"]}) if row["normalized_advantage"] is not None else None
                if set(game.objects(record, KG.baselineStatistics)) != ({baseline} if baseline else set()):
                    raise ValueError("Matchup baseline changed")
            else:
                link(hero, KG.hasStatistics, record)
                relations[(hero, KG.hasStatistics)].add(record)
                ranks = json.loads(row["rank_brackets"])
                targets = set()
                for index, source in enumerate(ranks):
                    node = URIRef(str(record) + f"/rank/{index}")
                    targets.add(node)
                    expect(node, KG.RankBracketObservation)
                    hero_name = next(h["display_name"] for h in rows["heroes"] if h["hero_id"] == row["hero_id"])
                    scalar(node, RDFS.label, f"{hero_name}: рейтинг {source['rank_bracket']}")
                    link(node, KG.hero, hero)
                    for name, value in source.items():
                        scalar(node, column_predicate("hero_meta", name), value)
                    counts["rank_observations_verified"] += 1
                if set(game.objects(record, KG.hasRankObservation)) != targets:
                    raise ValueError("Rank statistics relations changed")

    # Dense matrix cells are values owned directly by the hero. The row URI is
    # only the stable address prefix; no intermediate vector instance exists.
    for row in rows.get("hero_feature_matrix", []):
        hero = entity("hero", row["hero_id"])
        prefix = record_uri("hero_feature_matrix", row)
        wanted = set()
        for index, (code, value) in enumerate((k, v) for k, v in row.items() if k != "hero_id"):
            node = URIRef(str(prefix) + "/value/" + code)
            wanted.add(node)
            expect(node, KG.FeatureValue)
            feature = entity("feature", code)
            expect(feature, KG.Feature)
            link(hero, KG.hasFeatureValue, node)
            link(node, KG.feature, feature)
            scalar(node, KG.position, index)
            scalar(node, KG.value, value)
            evidence = features.get((row["hero_id"], code))
            if set(game.objects(node, KG.supportedByObservation)) != ({evidence} if evidence else set()):
                raise ValueError("Observed zero and matrix fill became indistinguishable")
            counts["matrix_values_verified"] += 1
        if set(game.objects(hero, KG.hasFeatureValue)) != wanted:
            raise ValueError("Direct hero feature values changed")

    units = {"ARMOR_PERCENTILE": "ratio", "BASE_MAGIC_RESISTANCE": "percent",
             "BASE_ARMOR": "armor", "MOVEMENT_SPEED": "game_distance_per_second",
             "ATTACK_RANGE": "game_distance", "MAX_AOE_RADIUS": "game_distance",
             "MAX_CONTROL_DURATION": "seconds"}
    for code in {r["feature"] for r in rows.get("hero_features", [])}:
        feature = entity("feature", code)
        kind = "count" if code.endswith("_COUNT") else "numeric" if code in NUMERIC_FEATURES else "binary"
        scalar(feature, KG.identifier, code)
        scalar(feature, KG.valueKind, kind)
        scalar(feature, KG.unit, units.get(code, "count" if kind == "count" else "indicator"))
        label = one(game, feature, RDFS.label)
        if str(label) != FEATURE_LABELS.get(code, code) or label.language != "ru":
            raise ValueError("Feature definition changed")
    for (owner, prop), targets in extra_relations.items():
        relations[(owner, prop)].update(targets)
    for cls, nodes in extra_expected.items():
        expected[cls].update(nodes)
    for (owner, prop), targets in relations.items():
        if set(game.objects(owner, prop)) != targets:
            raise ValueError("Extra or missing informative navigation relations")
    for cls in classes:
        if set(game.subjects(RDF.type, cls)) != expected[cls]:
            raise ValueError(f"Unexpected or orphan domain instances: {cls}")
    return {"triples": len(game), "classes": len(classes),
            "object_properties": len(set(game.subjects(RDF.type, OWL.ObjectProperty))),
            "data_properties": len(set(game.subjects(RDF.type, OWL.DatatypeProperty))),
            "records_verified": sum(len(nodes) for nodes in expected.values()),
            "domain_only": True, "short_identifiers": True,
            "anonymous_data_nodes": 0, "informative_values": dict(counts),
            "class_counts": {str(cls).rsplit('#', 1)[-1]: len(nodes) for cls, nodes in expected.items()}}
