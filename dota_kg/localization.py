"""Official Valve localization and translations of this application's own phrases."""
from functools import lru_cache
import html
import json
from pathlib import Path
import re

# Only phrases authored in this project, not excerpts from ability descriptions.
CUSTOM_PHRASES = {
    "Cleave требует атак ближнего боя.": "Рассекающий урон требует атак ближнего боя.",
    "Learn Marksmanship and keep every enemy hero outside its disable range. Procs ignore base armor only.": "Необходима изученная Marksmanship; в радиусе отключения способности не должно быть вражеских героев. Срабатывания игнорируют только базовую броню.",
    "A unit or tree behind the target enables the full stun; otherwise only the short stun applies.": "Для полного оглушения позади цели нужен другой юнит или дерево; иначе оглушение будет коротким.",
    "Marksmanship procs ignore base armor, not bonus armor; keep enemy heroes outside the disable range.": "Срабатывания Marksmanship игнорируют только базовую броню; в радиусе отключения способности не должно быть вражеских героев.",
    "Melee attacks can cleave grouped summoned units; damage and unit durability still matter.": "Атаки ближнего боя могут наносить рассекающий урон группе призванных существ; результат зависит от урона и стойкости существ.",
    "Evasion can avoid physical attacks while active; accuracy effects can bypass it.": "Уклонение позволяет избегать физических атак во время действия эффекта; точность может его преодолеть.",
    "Apply Static Charge to the attacked unit; retaliation procs can hit nearby summons.": "Наложите Static Charge на атакуемую цель; ответные молнии могут задеть ближайших призванных существ.",
    "Chain Lightning procs can damage multiple illusions; it is not an instant-kill effect.": "Цепные молнии могут наносить урон нескольким иллюзиям; они не уничтожают их мгновенно.",
    "Pure": "Чистый урон",
    "Magical": "Магический урон",
    "Physical": "Физический урон",
    "Basic": "Обычное развеивание",
    "Strong": "Сильное развеивание",
    "Здоровье врага ниже порога True Sight у Thirst.": "Здоровье врага ниже порога истинного зрения у Thirst.",
    "Требуется item_ultimate_scepter": "Требуется Aghanim's Scepter",
    "Требуется item_aghanims_shard": "Требуется Aghanim's Shard"
}


def normalized(text):
    text = '' if text is None else str(text)
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text))).strip()


@lru_cache(maxsize=1)
def _messages():
    official = json.loads((Path(__file__).parent / 'locales' / 'ru.json').read_text(encoding='utf-8'))
    return {**official, **CUSTOM_PHRASES}


def russian_text(text):
    """Use an official translation or an app phrase; retain unmatched source text."""
    text = '' if text is None else str(text)
    return _messages().get(normalized(text), text)
