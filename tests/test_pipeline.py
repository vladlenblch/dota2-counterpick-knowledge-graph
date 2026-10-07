import unittest

from dota_kg.dotabuff import parse_counters, parse_hero_meta, parse_items
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
        self.mapper = Mapper(heroes, [], [])
        self.heroes = heroes

    def test_dotabuff_disadvantage_points_against_page_hero(self):
        html = """<table><thead><tr><th>Hero</th><th>Disadvantage</th>
        <th>Phantom Lancer Hero Win Rate</th><th>Matches Played</th></tr></thead>
        <tbody><tr><td><a href='/heroes/axe'>Axe</a></td><td>5.52%</td>
        <td>47.48%</td><td>155,595</td></tr></tbody></table>"""
        rows = parse_counters(html, 12, self.mapper, "2026-10-07T00:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["hero_id"], rows[0]["opponent_hero_id"]), (12, 2))
        self.assertAlmostEqual(rows[0]["source_advantage"], .0552)
        self.assertAlmostEqual(rows[0]["normalized_advantage"], -.0552)
        self.assertIsNone(rows[0]["wins"])

    def test_illusion_dependency_is_explicit_manual_review(self):
        abilities = [{"hero_id": 12, "ability_id": 1, "display_name": "Juxtapose",
                      "description": "Creating an illusion of himself. Illusions also have a chance to fracture further.",
                      "damage_type_code": 0, "special_values": [], "damage": [], "duration": []}]
        rows, matrix = extract(self.heroes, abilities, overrides=[{
            "hero": "npc_dota_hero_phantom_lancer", "feature": "DEPENDS_ON_ILLUSIONS",
            "action": "ADD", "reason": "Repeated illusion generation reviewed manually",
        }])
        pl = {(row["feature"], row["derivation_method"]) for row in rows if row["hero_id"] == 12}
        self.assertIn(("CREATES_ILLUSIONS", "rule"), pl)
        self.assertIn(("DEPENDS_ON_ILLUSIONS", "manual"), pl)
        self.assertEqual(next(row for row in matrix if row["hero_id"] == 12)["CREATES_ILLUSIONS"], 1)

    def test_dotabuff_hero_meta_offline(self):
        html = """<table><thead><tr><th>Hero</th><th>Matches</th><th>Win Rate</th><th>Pick Rate</th></tr></thead>
        <tbody><tr><td><a href='/heroes/axe'>Axe</a></td><td>1,200</td><td>51.2%</td><td>12.1%</td></tr></tbody></table>"""
        rows = parse_hero_meta(html, self.mapper, "2026-10-07T00:00:00Z")
        self.assertEqual((rows[0]["hero_id"], rows[0]["matches"]), (2, 1200))
        self.assertAlmostEqual(rows[0]["pick_rate"], .121)

    def test_dotabuff_items_use_valve_identity(self):
        item = {"item_id": 1, "internal_name": "item_blink", "display_name": "Blink Dagger"}
        mapper = Mapper(self.heroes, [item], [])
        html = """<table><thead><tr><th>Item</th><th>Matches</th><th>Win Rate</th></tr></thead>
        <tbody><tr><td><a href='/items/blink-dagger'>Blink Dagger</a></td><td>700</td><td>49%</td></tr></tbody></table>"""
        rows = parse_items(html, 2, mapper, "2026-10-07T00:00:00Z")
        self.assertEqual((rows[0]["hero_id"], rows[0]["item_id"], rows[0]["matches"]), (2, 1, 700))

    def test_ambiguous_or_unknown_id_not_silently_mapped(self):
        self.assertIsNone(self.mapper.resolve("hero", numeric_id=999, source="test"))
        self.assertEqual(len(self.mapper.problems), 1)

    def test_preventing_blink_does_not_grant_blink(self):
        abilities = [{"hero_id": 2, "ability_id": 9, "display_name": "Trap",
                      "description": "Traps enemies in place, preventing movement or blinking.",
                      "damage_type_code": 0, "special_values": [], "damage": [], "duration": []}]
        rows, _ = extract(self.heroes, abilities, overrides=[])
        axe_features = {row["feature"] for row in rows if row["hero_id"] == 2}
        self.assertNotIn("BLINK", axe_features)
        self.assertNotIn("HIGH_MOBILITY", axe_features)


if __name__ == "__main__":
    unittest.main()
