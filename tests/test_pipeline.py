import unittest

from dota_kg.build import _merge_matchups
from dota_kg.features import extract
from dota_kg.mapping import Mapper


class PipelineTests(unittest.TestCase):
    def setUp(self):
        heroes = [
            {"hero_id": 12, "internal_name": "npc_dota_hero_phantom_lancer", "display_name": "Phantom Lancer",
             "armor": 3, "magic_resistance": 25, "movement_speed": 285, "attack_range": 150, "attack_type": "Melee"},
            {"hero_id": 2, "internal_name": "npc_dota_hero_axe", "display_name": "Axe",
             "armor": 4, "magic_resistance": 25, "movement_speed": 315, "attack_range": 150, "attack_type": "Melee"},
        ]
        self.mapper = Mapper(heroes, [])
        self.heroes = heroes

    def test_illusion_dependency_is_explicit_manual_review(self):
        abilities = [{"hero_id": 12, "ability_id": 1, "display_name": "Juxtapose",
                      "description": "Creating an illusion of himself. Illusions also have a chance to fracture further.",
                      "damage_type_code": 0, "special_values": [], "damage": [], "duration": []}]
        rows, matrix = extract(self.heroes, abilities, manual_rows=[{
            "hero_id": 12, "feature": "DEPENDS_ON_ILLUSIONS", "value": 1.0,
            "confidence": .85, "derivation_method": "manual",
            "evidence": "Repeated illusion generation reviewed manually",
        }])
        pl = {(row["feature"], row["derivation_method"]) for row in rows if row["hero_id"] == 12}
        self.assertIn(("CREATES_ILLUSIONS", "rule"), pl)
        self.assertIn(("DEPENDS_ON_ILLUSIONS", "manual"), pl)
        self.assertEqual(next(row for row in matrix if row["hero_id"] == 12)["CREATES_ILLUSIONS"], 1)

    def test_manual_feature_takes_precedence_over_automatic_rule(self):
        abilities = [{"hero_id": 2, "ability_id": 10, "display_name": "Slow",
                      "description": "Slows the target.", "damage_type_code": 0,
                      "special_values": [], "damage": [], "duration": []}]
        manual = {"hero_id": 2, "feature": "SLOW", "value": 1.0, "confidence": .8,
                  "derivation_method": "manual", "evidence": "Reviewed source description"}
        rows, _ = extract(self.heroes, abilities, manual_rows=[manual])
        self.assertEqual([row for row in rows if row["hero_id"] == 2 and row["feature"] == "SLOW"], [manual])

    def test_collected_matchup_replaces_manual_fill_when_available(self):
        collected = [{"hero_id": 12, "opponent_hero_id": 2, "source": "opendota", "wins": 5}]
        manual = [{"hero_id": 12, "opponent_hero_id": 2, "source": None, "wins": None},
                  {"hero_id": 2, "opponent_hero_id": 12, "source": None, "wins": None}]
        self.assertEqual(_merge_matchups(collected, manual), [collected[0], manual[1]])

    def test_ambiguous_or_unknown_id_not_silently_mapped(self):
        self.assertIsNone(self.mapper.resolve("hero", numeric_id=999, source="test"))
        self.assertEqual(len(self.mapper.problems), 1)

    def test_zero_counts_stay_in_matrix_without_creating_feature_relations(self):
        abilities = [{"hero_id": 2, "ability_id": 9, "display_name": "Stun",
                      "description": "Stuns the target.", "damage_type_code": 0,
                      "special_values": [], "damage": [], "duration": []}]
        rows, matrix = extract(self.heroes, abilities)
        self.assertTrue(all(row["value"] > 0 for row in rows if row["feature"].endswith("_COUNT")))
        self.assertEqual(next(row for row in rows if row["hero_id"] == 2 and row["feature"] == "STUN_COUNT")["value"], 1)
        self.assertEqual(next(row for row in matrix if row["hero_id"] == 12)["STUN_COUNT"], 0)

    def test_preventing_blink_does_not_grant_blink(self):
        abilities = [{"hero_id": 2, "ability_id": 9, "display_name": "Trap",
                      "description": "Traps enemies in place, preventing movement or blinking.",
                      "damage_type_code": 0, "special_values": [], "damage": [], "duration": []}]
        rows, _ = extract(self.heroes, abilities)
        axe_features = {row["feature"] for row in rows if row["hero_id"] == 2}
        self.assertNotIn("BLINK", axe_features)
        self.assertNotIn("HIGH_MOBILITY", axe_features)


if __name__ == "__main__":
    unittest.main()
