"""Russian output covers saved prose and preserves its numeric template keys."""
import json
from pathlib import Path
import re
import unittest

import pyarrow.parquet as pq

from dota_kg.localization import russian_text, normalized, CUSTOM_PHRASES

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'\{s:([^}]+)\}|%([A-Za-z_][A-Za-z_0-9]*)%')


class LocalizationTests(unittest.TestCase):
    def test_saved_prose_uses_official_translations_or_unchanged_source(self):
        checked=unchanged=0
        source_prose=set()
        for table in ('abilities','items'):
            for row in pq.read_table(ROOT/'data/final'/f'{table}.parquet').to_pylist():
                texts=[row.get(field) for field in ('description','scepter_description','shard_description')]
                texts+=json.loads(row.get('notes') or '[]')
                texts+=json.loads(row['raw']).get('facets_loc',[])
                for text in texts:
                    if text:
                        source_prose.add(normalized(text))
                    if text and re.search(r'[A-Za-z]{3}',text) and not re.search('[А-Яа-яЁё]',text):
                        translated=russian_text(text)
                        if translated == text:
                            unchanged+=1
                        else:
                            self.assertRegex(translated,'[А-Яа-яЁё]',(row['internal_name'],text))
                            checked+=1
        self.assertGreater(checked,2000)
        self.assertGreater(unchanged,0)
        self.assertFalse(set(CUSTOM_PHRASES) & source_prose)

    def test_all_translation_templates_keep_exact_parameter_identifiers(self):
        messages=json.loads((ROOT/'dota_kg/locales/ru.json').read_text())
        keys=lambda text:{a or b for a,b in TOKEN.findall(text)}
        for english,russian in messages.items():
            self.assertFalse(english.startswith('@summary:'))
            self.assertEqual(keys(english),keys(russian),english)

    def test_russian_text_is_idempotent_and_unknown_russian_is_preserved(self):
        for text in ('За целью должно быть дерево.','Cleave требует атак ближнего боя.'):
            translated=russian_text(text)
            self.assertEqual(russian_text(translated),translated)
        self.assertEqual(russian_text('Новая русская формулировка.'),'Новая русская формулировка.')

    def test_numeric_zero_is_preserved(self):
        from rdflib import Literal
        self.assertEqual(russian_text(0),'0')
        self.assertEqual(russian_text(Literal(0)),'0')

    def test_game_names_are_not_partially_translated(self):
        self.assertEqual(russian_text('Gem of True Sight'),'Gem of True Sight')
        self.assertEqual(russian_text('Эффект предоставляет True Sight.'),'Эффект предоставляет True Sight.')

    def test_translation_keeps_saved_range_and_dynamic_upgrade_numbers(self):
        self.assertIn('1110',russian_text('Can hit units up to 1110 range away.'))
        self.assertIn('%tooltip_scepter_bonus%',russian_text('Increases Base Strength Reduction by %tooltip_scepter_bonus%.'))

    def test_official_translation_and_custom_phrases_have_separate_fallbacks(self):
        self.assertEqual(russian_text("Fissure is not blocked by Linken's Sphere."),
                         "Linken's Sphere не блокирует эту способность.")
        self.assertEqual(russian_text('Both shapes have an independent cooldown.'),
                         'Both shapes have an independent cooldown.')
        self.assertEqual(russian_text('Arcane Orb bounces to an additional target for %bounce_damage_reduction%%% less damage.'),
                         'Arcane Orb bounces to an additional target for %bounce_damage_reduction%%% less damage.')
        self.assertEqual(russian_text('Cleave требует атак ближнего боя.'),
                         'Рассекающий урон требует атак ближнего боя.')
