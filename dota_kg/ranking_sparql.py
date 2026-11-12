"""Scoped RDFLib SPARQL functions backed by the same model as text questions."""
from contextlib import contextmanager
from threading import RLock

from rdflib import Literal, Namespace, URIRef
from rdflib.graph import ReadOnlyGraphAggregate
from rdflib.plugins.sparql.operators import register_custom_function, unregister_custom_function
from rdflib.plugins.sparql.sparql import SPARQLError

from .ontology_schema import BASE
from .ranking import RankingModel

FN = Namespace(BASE + 'function/')
FUNCTIONS = {FN[name] for name in ('score', 'effect', 'rule', 'target',
                                  'ownerScore', 'ownerEffect', 'ownerRule', 'ownerTarget')}
_LOCK = RLock()


def ranking_functions(value):
    if isinstance(value, URIRef) and value in FUNCTIONS:
        return {value}
    if isinstance(value, dict):
        return set().union(*(ranking_functions(v) for v in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(ranking_functions(v) for v in value))
    return set()


@contextmanager
def ranking_query(graph, *, own_items=(), enemy_items=(), level=18, actor=None, position=None, phase=None):
    """Restore the process registry after evaluation, including on exceptions."""
    model = RankingModel(graph, own_items=own_items, enemy_items=enemy_items, level=level)
    # Build the small derived-fact view before SPARQL joins can inspect it.
    # The stored ontology is never modified.
    for enemy in model.heroes:
        for need in model.profile(enemy, model.enemy_items)[3].values():
            model.target_node(need, enemy)
    from .item_ranking import ItemRanking
    purchases = None
    owners, purchase_lists = {}, {}

    def evaluate(mode, field, source, enemy, family=None):
        nonlocal purchases
        if mode == 'hero':
            assessment = model.assess(source, enemy)
            if assessment is None:
                raise SPARQLError('Нет применимого механического основания')
            value, primary = assessment.score, assessment.primary
        else:
            key = source, enemy, str(family) if family is not None else None
            if key not in owners:
                hero = model.ability_hero.get(source, actor)
                focus = set(str(family).split(',')) if family is not None else None
                if source in model.items:
                    if purchases is None:
                        purchases = ItemRanking(model)
                    # A named SPARQL source is enumeration, not an explicitly
                    # requested exception to purchase suitability.
                    purchase_key = hero, enemy, str(family)
                    if purchase_key not in purchase_lists:
                        purchase_lists[purchase_key] = {row.item: row for row in purchases.rank(hero, enemy,
                            position=position, phase=phase, focus=focus)}
                    eligible = purchase_lists[purchase_key]
                    row = eligible.get(source)
                    owners[key] = (row.score, row.primary) if row else (0., None)
                else:
                    apps = model.applications(hero, enemy, owner=source, focus=focus)
                    total, groups = model.aggregate(apps)
                    owners[key] = total, groups[0]['applications'][0] if groups else None
            value, primary = owners[key]
            if primary is None:
                raise SPARQLError('Нет применимого механического основания')
        if field == 'score':
            return Literal(round(value, 6))
        if field == 'effect':
            return primary.fact.uri
        if field == 'rule':
            return primary.rule.uri
        return model.target_node(primary, enemy)

    registered = []
    with _LOCK:
        try:
            for mode, prefix in (('hero', ''), ('owner', 'owner')):
                for field in ('score', 'effect', 'rule', 'target'):
                    name = prefix + field.capitalize() if prefix else field
                    def function(source, enemy, family=None, *, _mode=mode, _field=field):
                        return evaluate(_mode, _field, source, enemy, family)
                    uri = FN[name]
                    register_custom_function(uri, function)
                    registered.append(uri)
            yield ReadOnlyGraphAggregate([graph, model.view])
        finally:
            for uri in reversed(registered):
                unregister_custom_function(uri)
