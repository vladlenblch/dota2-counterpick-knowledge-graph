"""Resolve local names and route Russian questions; no game answers live here."""
from dataclasses import dataclass
import re

from rdflib import RDF, RDFS, URIRef

from .ontology_schema import KG


# Language aliases only. Each canonical name must resolve to an entity in the graph.
ALIASES = {
    "Phantom Lancer": "фантом лансер|пл|лансер",
    "Phantom Assassin": "фантом ассасин|фантомка|па|мортра",
    "Anti-Mage": "анти маг|антимаг|ам",
    "Crystal Maiden": "кристал мейден|цм|цмка|кристалка",
    "Drow Ranger": "дроу рейнджер|дровка|тракса|дроу",
    "Ancient Apparition": "эншент аппаратишн|аппарат|аа",
    "Windranger": "виндренджер|вр|врка|винда",
    "Lion": "лион|лайон", "Rubick": "рубик|рубикк",
    "Beastmaster": "бистмастер|бист", "Enigma": "энигма",
    "Abaddon": "абаддон|абадон", "Juggernaut": "джаггернаут|джагер|джаггер|юра",
    "Terrorblade": "террорблейд|террор|тб", "Ursa": "урса",
    "Morphling": "морфлинг|морф", "Lich": "лич",
    "Storm Spirit": "шторм спирит|шторм", "Sven": "свен",
    "Pugna": "пугна", "Slardar": "слардар|селедка",
    "Bounty Hunter": "баунти хантер|баунти|бх|гондар",
    "Zeus": "зевс", "Bloodseeker": "бладсикер|сикер",
    "Legion Commander": "легион коммандер|легионка|лк",
    "Oracle": "оракл", "Slark": "сларк", "Silencer": "сайленсер|сало",
    "Lina": "лина", "Shadow Shaman": "шадоу шаман|шаман|раста",
    "Axe": "акс|могул", "Pudge": "пудж", "Invoker": "инвокер|вокер",
    "Earthshaker": "эртшейкер|шейкер", "Sand King": "сэнд кинг|ск",
    "Tiny": "тини", "Leshrac": "лешрак|леший", "Puck": "пак",
    "Ember Spirit": "эмбер спирит|эмбер", "Void Spirit": "войд спирит",
    "Faceless Void": "фейслесс войд|войд", "Luna": "луна",
    "Medusa": "медуза|дуза", "Sniper": "снайпер", "Dazzle": "даззл|дазл",
    "Witch Doctor": "витч доктор|вд|доктор", "Jakiro": "джакиро|жакиро",
    "Warlock": "варлок", "Disruptor": "дизраптор", "Ogre Magi": "огр маг|огр",
    "Black King Bar": "бкб|bkb", "Monkey King Bar": "мкб|mkb",
    "Linken's Sphere": "линка|линкен", "Manta Style": "манта",
    "Battle Fury": "бф|бфури|батл фьюри", "Mjollnir": "мьелнир|молнии",
    "Scythe of Vyse": "хекс|коса", "Orchid Malevolence": "орчид",
    "Gem of True Sight": "гем", "Dust of Appearance": "даст|дасты",
    "Sentry Ward": "сентри", "Lotus Orb": "лотус", "Buckler": "баклер",
    "Assault Cuirass": "кираса", "Aghanim's Scepter": "аганим|септер|scepter",
    "Aghanim's Shard": "шард|shard",
}

ROLES = {"carry": "Carry (керри / мид)", "nuker": "Nuker (тройка)",
         "support": "Support (позиции 4 / 5)"}
ROLE_PATTERNS = {
    "support": r"хард\s+саппорт\w*|полн\w*\s+поддерж\w*|саппорт\w*|поддерж\w*|четверк\w*|пятерк\w*|support|позиц\w*\s*[45]",
    "carry": r"керри|кэрри|carry|мид|миде|миду|мидер\w*|mid|позиц\w*\s*[12]",
    "nuker": r"нюкер\w*|nuker|офф?ле[йи]н\w*|хард[еау]?(?!\s+саппорт)|тройк\w*|позиц\w*\s*3",
}


def normalize(text):
    return re.sub(r"[^\w\s]", " ", text.casefold().replace("ё", "е")).strip()


def alias_pattern(alias):
    words = normalize(alias).split()
    last = words[-1]
    if re.fullmatch(r"[а-я]{4,}", last):
        # shortcut: common Russian case endings, not a general morphology engine.
        if last.endswith("а"):
            last = re.escape(last[:-1]) + r"(?:а|ы|е|у|ой|ою)"
        elif last[-1] not in "еиоуыэюяьй":
            last = re.escape(last) + r"(?:а|у|ом|е|ы|и)?"
    return r"(?<!\w)" + r"\s+".join([*(re.escape(w) for w in words[:-1]), last]) + r"(?!\w)"


@dataclass
class Question:
    cq: int
    hero: URIRef | None = None
    enemy: URIRef | None = None
    subject: URIRef | None = None
    item: URIRef | None = None
    role: str | None = None
    phase: str | None = None
    active: bool | None = None
    ally: bool | None = None
    upgrade: str | None = None


class QuestionParser:
    def __init__(self, graph):
        self.graph = graph
        aliases = {}
        for cls in (KG.Hero, KG.Ability, KG.Item):
            for entity in graph.subjects(RDF.type, cls):
                for label in graph.objects(entity, RDFS.label):
                    aliases.setdefault(normalize(str(label)), set()).add(entity)
                for label in graph.objects(entity, KG.alternativeName):
                    aliases.setdefault(normalize(str(label)), set()).add(entity)
        for canonical, variants in ALIASES.items():
            targets = aliases.get(normalize(canonical), set())
            for variant in variants.split("|"):
                if targets:
                    aliases.setdefault(normalize(variant), set()).update(targets)
        self.aliases = [(re.compile(alias_pattern(alias)), entities)
                        for alias, entities in aliases.items() if alias]

    def entities(self, text):
        matches = [(m.start(), m.end(), entities) for pattern, entities in self.aliases
                   for m in pattern.finditer(text)]
        selected = []
        for start, end, entities in sorted(matches, key=lambda m: (-(m[1]-m[0]), m[0])):
            if any(start < b and end > a for a, b, _ in selected):
                continue
            if len(entities) != 1:
                names = sorted(str(self.graph.value(e, RDFS.label)) for e in entities)
                raise ValueError(f"Неоднозначное название «{text[start:end]}»: {', '.join(names)}. Укажите полное имя.")
            selected.append((start, end, next(iter(entities))))
        return sorted(selected)

    def parse(self, text):
        text = normalize(text)
        if not text:
            raise ValueError("Введите вопрос о героях, способностях или предметах.")
        found = self.entities(text)
        has_item = any((entity, RDF.type, KG.Item) in self.graph for _, _, entity in found)
        # Routing order prevents e.g. an enemy BKB question being treated as CQ08.
        if re.search(r"талант|аспект|аганим|септер|шард|scepter|shard", text):
            cq = 12
        elif re.search(r"услови|ограничени|дистанци|когда срабатыв|что нужно для", text):
            cq = 3
        elif (has_item and re.search(r"у противник", text) or re.search(
                r"противник\w*.*(?:предмет|купил|бкб|bkb|мкб|mkb)|влияет.*(?:предмет|уклон)|активн\w* (?:бкб|bkb)", text)):
            cq = 11
        elif re.search(r"сквозь|невосприимчив|проход\w*.*(?:бкб|bkb)", text):
            cq = 8
        elif re.search(r"снять|снима\w*|развеив|диспел", text):
            cq = 7
        elif re.search(r"компенсир|уязвим|слабост|повысить.*брон|низк\w* брон", text):
            cq = 10
        elif re.search(r"купить|покуп\w*|какие предметы.*(?:геро|против)|что собр", text):
            cq = 9
        elif re.search(r"прерва|прерыва|сбить|сбива", text):
            cq = 4
        elif re.search(r"обнаруж|невидим|инвиз|детект", text):
            cq = 6
        elif re.search(r"иллюзи|призван|саммон", text):
            cq = 5
        elif re.search(r"почему|объясн|какие.*(?:способности|механики).*преимущ", text):
            cq = 2
        elif re.search(r"кто контрит|кого взять|какие герои|кто.*против|контрпик|противодейств", text):
            cq = 1
        else:
            raise ValueError("Не удалось определить задачу. Например: «кто контрит ПЛ на миде» или «что купить Rubick против ПЛ».")
        heroes = [(a, b, e) for a, b, e in found if (e, RDF.type, KG.Hero) in self.graph]
        abilities = [e for _, _, e in found if (e, RDF.type, KG.Ability) in self.graph]
        items = [e for _, _, e in found if (e, RDF.type, KG.Item) in self.graph]
        if len(heroes) > 2 or len(set(items)) > 1 or len(set(abilities)) > 1:
            raise ValueError("Уточните вопрос: поддерживаются до двух героев, одна способность и один предмет.")
        q = Question(cq)
        roles = [role for role, pattern in ROLE_PATTERNS.items()
                 if re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", text)]
        if len(roles) > 1:
            raise ValueError("Укажите одну группу ролей или уберите ограничение для поиска по всем группам.")
        q.role = roles[0] if roles else None
        for phase, pattern in (("early", r"начал|ранн"), ("late", r"поздн|лейт|late"),
                               ("mid", r"середин|средн\w* стади|мидгейм")):
            if re.search(pattern, text):
                q.phase = phase
                break
        q.subject = abilities[0] if abilities else None
        q.item = items[0] if items else None
        unique_heroes = list(dict.fromkeys(e for _, _, e in heroes))
        if len(unique_heroes) == 2:
            marker = re.search(r"\bпротив\b", text)
            after = [e for a, _, e in heroes if marker and a > marker.start()]
            q.enemy = after[0] if after else unique_heroes[1]
            q.hero = next(e for e in unique_heroes if e != q.enemy)
        elif unique_heroes:
            if cq in (1, 4, 5, 6, 7, 8):
                q.enemy = unique_heroes[0]
            else:
                q.hero = unique_heroes[0]
        if cq in (3, 7) and q.subject is None and q.item:
            q.subject, q.item = q.item, None
        if cq == 4 and q.enemy and re.search(r"может ли|сможет ли", text):
            q.hero, q.enemy = q.enemy, None
        if cq == 3 and q.subject is None:
            q.subject = q.hero
        if cq == 11:
            q.active = False if re.search(r"не актив|не включ|выключ", text) else (
                True if re.search(r"актив|включ", text) else None)
        if cq == 7:
            q.ally = True if re.search(r"союзник", text) else (False if re.search(r"себя|себе", text) else None)
        if cq == 12:
            upgrades = [kind for kind, pattern in (
                ("shard", "шард|shard"), ("scepter", "аганим|септер|scepter"),
                ("talent", "талант"), ("facet", "аспект")) if re.search(pattern, text)]
            q.upgrade = upgrades[0] if len(upgrades) == 1 else None
        if cq == 1 and q.enemy is None:
            raise ValueError("Укажите противника: «кто контрит Фантом Лансера».")
        if cq == 2 and (q.hero is None or q.enemy is None):
            raise ValueError("Укажите двух героев: «почему Lion контрит Crystal Maiden».")
        if cq == 3 and q.subject is None:
            raise ValueError("Укажите способность, предмет или героя, чьи условия нужно проверить.")
        if cq in (9, 11) and q.hero is None:
            raise ValueError("Укажите своего героя.")
        if cq == 9 and q.enemy is None:
            raise ValueError("Укажите героя и противника: «что купить Rubick против ПЛ».")
        if cq == 10 and q.hero is None:
            raise ValueError("Укажите героя, чьи уязвимости нужно компенсировать.")
        if cq == 11 and q.item is None:
            raise ValueError("Укажите предмет противника.")
        if cq == 12 and q.hero is None and q.subject is None:
            raise ValueError("Укажите героя или способность для проверки улучшений.")
        if q.hero and q.subject and cq in (2, 11, 12) and (q.hero, KG.hasAbility, q.subject) not in self.graph:
            raise ValueError("Указанная способность не принадлежит выбранному герою в графе.")
        return q
