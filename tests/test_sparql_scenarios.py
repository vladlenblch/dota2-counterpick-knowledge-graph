"""SPARQL scenarios checked against the complete saved game dataset.

Run the integration checks explicitly:
python -m unittest discover -s tests -p test_sparql_scenarios.py
"""
from pathlib import Path
import unittest

import pyarrow.parquet as pq

from rdflib import Graph, Literal, RDF, RDFS, XSD

from dota_kg.ontology import load_ontology, literal
from dota_kg.ontology_schema import KG, RES, evidence_text

COMPETENCY_QUERIES = {
    "inventory": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
SELECT ?class (COUNT(?entity) AS ?count)
WHERE {
  VALUES ?class { kg:Hero kg:Ability kg:Item kg:Feature kg:FeatureValue
                  kg:MatchupObservation kg:ItemUsageObservation }
  ?entity a ?class .
}
GROUP BY ?class
ORDER BY ?class
""",
    "rubick_features": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX hero: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/hero/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?code ?value ?evidence ?abilityName
WHERE {
  ?observation a kg:FeatureValue ;
    kg:hero hero:86 ; kg:feature ?feature ; kg:value ?value ; kg:evidence ?evidence ; kg:isObserved true .
  ?feature kg:identifier ?code .
  OPTIONAL { ?observation kg:supportedByAbility ?ability . ?ability rdfs:label ?abilityName . }
}
ORDER BY ?code
""",
    "forced_movement": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX feature: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/feature/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?id ?name ?evidence
WHERE {
  ?observation a kg:FeatureValue ;
    kg:feature feature:FORCED_MOVEMENT ; kg:hero ?hero ; kg:value ?value ; kg:evidence ?evidence ; kg:isObserved true .
  FILTER (?value = 1.0e0)
  ?hero kg:identifier ?id ; rdfs:label ?name .
}
ORDER BY ?id
""",
    "rubick_late_items": """PREFIX kg: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/ontology#>
PREFIX hero: <https://github.com/vladlenblch/dota2-counterpick-knowledge-graph/resource/hero/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?id ?name ?uses
WHERE {
  ?observation a kg:ItemUsageObservation ; kg:hero hero:86 ;
    kg:phase ?phase ; kg:item ?item ; kg:usageCount ?uses .
  FILTER (?phase = "late")
  ?item kg:identifier ?id ; rdfs:label ?name .
}
ORDER BY DESC(?uses) ?name ?id
LIMIT 5
""",
}


def check_scenarios(graph, root: Path):
    """Compare four test SPARQL results with the project datasets."""
    root = Path(root)
    read = lambda name: pq.read_table(root / f"data/final/{name}.parquet").to_pylist()
    heroes = {r["hero_id"]: r["display_name"] for r in read("heroes")}
    items = {r["item_id"]: r["display_name"] for r in read("items")}
    features = read("hero_features")
    inventory = {
        "Hero": len(heroes), "Ability": len(read("abilities")), "Item": len(items),
        "Feature": len({r["feature"] for r in features}),
        "MatchupObservation": len(read("matchups")), "ItemUsageObservation": len(read("hero_items")),
        "FeatureValue": sum(len(row) - 1 for row in read("hero_feature_matrix")),
    }
    rubick = sorted((r["feature"], r["value"], evidence_text(r["evidence"])) for r in features if r["hero_id"] == 86)
    forced = sorted((r["hero_id"], heroes[r["hero_id"]], evidence_text(r["evidence"]))
                    for r in features if r["feature"] == "FORCED_MOVEMENT" and r["value"] == 1)
    top_items = sorted((r for r in read("hero_items") if r["hero_id"] == 86 and r["phase"] == "late"),
                       key=lambda r: (-r["usage_count"], items[r["item_id"]], r["item_id"]))[:5]
    expected = {
        "inventory": sorted((str(KG[key]), count) for key, count in inventory.items()),
        "rubick_features": rubick,
        "forced_movement": forced,
        "rubick_late_items": [(r["item_id"], items[r["item_id"]], r["usage_count"]) for r in top_items],
    }
    results = {}
    for name, original in expected.items():
        query = COMPETENCY_QUERIES[name]
        actual = [tuple(value.toPython() if value is not None else None for value in row)
                  for row in graph.query(query)]
        if name == "rubick_features":
            comparable = [row[:3] for row in actual]
        else: comparable = actual
        if comparable != original:
            raise ValueError(f"SPARQL {name} differs from Parquet: {comparable!r} != {original!r}")
        results[name] = {"passed": True, "rows": len(actual), "result": actual}
    return results


class DatasetSparqlScenarios(unittest.TestCase):
    def test_scenarios_match_saved_datasets(self):
        root = Path(__file__).resolve().parents[1]
        result = check_scenarios(load_ontology(root / "ontology"), root)
        self.assertTrue(all(check["passed"] for check in result.values()))
        self.assertEqual(len(result), 4)

    def test_feature_query_compares_numeric_values_across_double_lexical_forms(self):
        movement_query = COMPETENCY_QUERIES["forced_movement"]
        for lexical in ("1.0e0", "1.0"):
            with self.subTest(lexical=lexical):
                graph = Graph()
                for identifier, name, value, evidence in (
                        (86, "Rubick", lexical, "Telekinesis moves the target"),
                        (1, "Other hero", "0.0", "No displacement")):
                    hero = RES[f"hero/{identifier}"]
                    observation = RES[f"testObservation/{identifier}/FORCED_MOVEMENT"]
                    for triple in (
                            (hero, KG.identifier, literal(identifier)),
                            (hero, RDFS.label, literal(name)),
                            (observation, RDF.type, KG.FeatureValue),
                            (observation, KG.hero, hero),
                            (observation, KG.feature, RES["feature/FORCED_MOVEMENT"]),
                            (observation, KG.value, Literal(value, datatype=XSD.double, normalize=False)),
                            (observation, KG.isObserved, literal(True)),
                            (observation, KG.evidence, literal(evidence))):
                        graph.add(triple)
                movement = [tuple(value.toPython() for value in row)
                            for row in graph.query(movement_query)]
                self.assertEqual(movement, [(86, "Rubick", "Telekinesis moves the target")])
                self.assertEqual(str(graph.value(RES["testObservation/86/FORCED_MOVEMENT"], KG.value)), lexical)

    def test_item_query_accepts_plain_and_typed_string_phases(self):
        query = COMPETENCY_QUERIES["rubick_late_items"]
        for phase in (Literal("late"), Literal("late", datatype=XSD.string)):
            with self.subTest(datatype=phase.datatype):
                graph = Graph()
                item = RES["item/1"]
                graph.add((item, KG.identifier, literal(1)))
                graph.add((item, RDFS.label, literal("Blink Dagger")))
                for identifier, observed_phase, uses in (
                        ("late", phase, 42), ("early", Literal("early", datatype=XSD.string), 999)):
                    observation = RES["testItemUsage/" + identifier]
                    for triple in (
                            (observation, RDF.type, KG.ItemUsageObservation),
                            (observation, KG.hero, RES["hero/86"]),
                            (observation, KG.item, item),
                            (observation, KG.phase, observed_phase),
                            (observation, KG.usageCount, literal(uses))):
                        graph.add(triple)
                rows = [tuple(value.toPython() for value in row) for row in graph.query(query)]
                self.assertEqual(rows, [(1, "Blink Dagger", 42)])


if __name__ == "__main__":
    unittest.main()
