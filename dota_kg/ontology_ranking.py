"""Validate and export the ordinary dataset of weighted mechanic interactions."""
import json
import math
import re

from rdflib import Graph, RDF, RDFS, URIRef

from .ontology import literal
from .ontology_schema import KG, RES, bind
from .ontology_mechanics import SCOPES


def build_weighted_rules(game, rows):
    graph = bind(Graph())
    catalog = {row['mechanic']: row for row in rows.get('mechanic_catalog', [])}
    keys = set()
    for row in rows.get('counter_rules', []):
        if (set(row) != {'source_mechanic', 'target_mechanic', 'family', 'weight', 'source_scopes', 'detail_ru'}
                or row['source_mechanic'] not in catalog or row['target_mechanic'] not in catalog
                or not re.fullmatch(r'[a-z][a-z-]*', row['family'])
                or type(row['weight']) is not float or not math.isfinite(row['weight']) or row['weight'] <= 0
                or not row['detail_ru']):
            raise ValueError('Invalid weighted counter rule')
        key = row['source_mechanic'], row['target_mechanic']
        if key in keys:
            raise ValueError('Duplicate weighted counter rule')
        keys.add(key)
        scopes = json.loads(row['source_scopes'])
        if (not isinstance(scopes, list) or not scopes or any(scope not in SCOPES for scope in scopes)
                or len(set(scopes)) != len(scopes)):
            raise ValueError('Invalid recipients in a weighted counter rule')
        rule = RES[f'counter-rule/{key[0]}/{key[1]}']
        if (rule, RDF.type, KG.CounterRule) not in game:
            graph.add((rule, RDF.type, KG.CounterRule))
            graph.add((rule, RDFS.label, literal(row['detail_ru'])))
            graph.add((rule, KG.evidence, literal(row['detail_ru'])))
            graph.add((rule, KG.ruleStatus, literal('heuristic')))
            graph.add((rule, KG.rulePurpose, literal('counter')))
            condition = URIRef(f'{rule}/condition/applicability')
            graph.add((rule, KG.requiresCondition, condition))
            graph.add((condition, RDF.type, KG.InteractionCondition))
            graph.add((condition, RDFS.label, literal(row['detail_ru'])))
            for prop, value in ((KG.conditionKind, 'review'), (KG.conditionScope, 'target'),
                                (KG.conditionText, row['detail_ru'])):
                graph.add((condition, prop, literal(value)))
        for field, prop in (('source_mechanic', KG.sourceMechanic), ('target_mechanic', KG.targetMechanic)):
            graph.add((rule, prop, RES[f'mechanic/{row[field]}']))
        graph.add((rule, KG.ruleWeight, literal(row['weight'])))
        graph.add((rule, KG.ruleFamily, literal(row['family'])))
        for scope in scopes:
            graph.add((rule, KG.sourceEffectTarget, literal(scope)))
    return graph
