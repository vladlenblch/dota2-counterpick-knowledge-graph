"""Render Valve tooltip values from RDF without guessing modifier arithmetic."""
from decimal import Decimal
import html
import math
import re

from rdflib import RDFS

from .ontology_schema import KG
from .localization import russian_text

_TOKEN = re.compile(r'\{s:([A-Za-z_][A-Za-z0-9_]*)\}|%([A-Za-z_][A-Za-z0-9_]*)%')


def _value(graph, node, predicate, default=None):
    value = graph.value(node, predicate)
    return value.toPython() if value is not None else default


def _series(graph, node):
    values = sorted(graph.objects(node, KG.hasBaseValue),
                    key=lambda value: (_value(graph, value, KG.position, 0), str(value)))
    return tuple(_value(graph, value, KG.numericValue) for value in values)


def _key(name):
    return re.sub(r'[^a-z0-9]', '', str(name).casefold())


def _display(values):
    if not isinstance(values, (list, tuple)):
        values = (values,)
    if not values:
        return None
    formatted = []
    for value in values:
        if hasattr(value, 'toPython'):
            value = value.toPython()
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return None
        if not math.isfinite(value):
            return None
        if isinstance(value, Decimal):
            formatted.append(format(value.normalize(), 'f'))
        else:
            formatted.append(format(value, 'g'))
    return formatted[0] if len(set(formatted)) == 1 else '/'.join(formatted)


def _parameters(graph, owner):
    return [(parameter, str(_value(graph, parameter, KG.parameterName, '')))
            for parameter in sorted(set(graph.objects(owner, KG.hasParameter)), key=str)]


def _known_values(records):
    """Conflicting observations are not resolved by whichever RDF row comes first."""
    candidates = {}
    for _, name, values in records:
        rendered = _display(values)
        if name and rendered is not None:
            candidates.setdefault(_key(name), {}).setdefault(rendered, values)
    return {key: next(iter(variants.values())) for key, variants in candidates.items() if len(variants) == 1}


def render_game_text(graph, owner, text, *, values=None):
    """Replace known tokens; preserve unknown ones instead of inventing zeroes.

    ``values`` maps parameter names to literal values or level vectors. Overrides
    do not mean addition: callers provide the relevant bonus/facet series itself.
    Talent ``bonus_`` tokens resolve only from overrides, never from base damage.
    """
    base = {key: _display(values) for key, values in _known_values(
        (parameter, name, _series(graph, parameter)) for parameter, name in _parameters(graph, owner)
        if not _value(graph, parameter, KG.requiredFacet, '')).items()}
    overrides = {}
    for name, value in (values or {}).items():
        rendered = _display(value)
        if rendered is not None:
            overrides[_key(name)] = rendered

    def substitute(match):
        name = match.group(1) or match.group(2)
        key = _key(name)
        if match.group(1) and name.casefold().startswith('bonus_'):
            return overrides.get(_key(name[6:]), match.group(0))
        if key in overrides:
            return overrides[key]
        if name.casefold().startswith('bonus_'):
            bonus = overrides.get(_key(name[6:]))
            if bonus is not None:
                return bonus
        return base.get(key, match.group(0))

    rendered = _TOKEN.sub(substitute, html.unescape(str(text or '')))
    rendered = re.sub(r'<br\s*/?>|</(?:h[1-6]|p|div)>', ' ', rendered, flags=re.I)
    rendered = re.sub(r'</?[A-Za-z][^>]*>', '', rendered)
    return re.sub(r'\s+', ' ', rendered.replace('%%', '%')).strip()


def render_russian_game_text(graph, owner, text, *, values=None):
    """Render the official translation, retaining the original if it is absent."""
    return render_game_text(graph, owner, russian_text(text), values=values)


def _modifier_records(graph, owner, *, talent=None, facet=None):
    result = []
    for parameter, name in _parameters(graph, owner):
        matches_for_parameter = []
        for modifier in sorted(graph.objects(parameter, KG.hasModifier), key=str):
            if talent is not None:
                matches = graph.value(modifier, KG.requiresTalent) == talent
            else:
                matches = (_value(graph, modifier, KG.modifierKind) == 'facet'
                           and _value(graph, modifier, KG.conditionName) == facet)
            if matches:
                scalar = _value(graph, modifier, KG.numericValue)
                values = (scalar,) if scalar is not None else _series(graph, modifier)
                matches_for_parameter.append((parameter, name, values))
        if matches_for_parameter:
            result.extend(matches_for_parameter)
        elif facet is not None and _value(graph, parameter, KG.requiredFacet) == facet:
            result.append((parameter, name, _series(graph, parameter)))
    return result


def _fallback(graph, records, prefix):
    rows = []
    for parameter, name, values in records:
        rendered = _display(values)
        if rendered is None:
            continue
        heading = _value(graph, parameter, KG.parameterHeading, '')
        label = re.sub(r'<[^>]+>|\$', '', russian_text(heading)).strip(' :')
        label = label or russian_text(name)
        if label:
            rows.append(f'{label}: {rendered}')
    return prefix + '; '.join(dict.fromkeys(rows)) if rows else ''


def talent_text(graph, owner, talent):
    """Resolve all bonus placeholders from this ability and this talent together."""
    labels = sorted(graph.objects(talent, RDFS.label), key=lambda label: (
        not (getattr(label, 'language', None) or '').lower().startswith('ru'), str(label)))
    text = russian_text(labels[0]) if labels else ''
    records = _modifier_records(graph, owner, talent=talent)
    values = _known_values(records)
    rendered = render_game_text(graph, owner, text, values=values)
    if rendered and not _TOKEN.search(rendered):
        return rendered
    return _fallback(graph, records, 'Бонусы таланта: ')


def facet_text(graph, owner, facet_name):
    """Use a facet note only when its parameter tokens identify this facet.

    An unlabelled prose note is not associated with an arbitrary facet by index.
    Raw facet values remain available when reliable prose matching is impossible.
    Operation codes are never interpreted as an instruction to add to the base.
    """
    facet_name = str(facet_name)
    records = _modifier_records(graph, owner, facet=facet_name)
    values = _known_values(records)
    if not values:
        return ''
    facets = {}
    for parameter, name in _parameters(graph, owner):
        required = _value(graph, parameter, KG.requiredFacet)
        if required:
            facets.setdefault(required, set()).add(_key(name))
        for modifier in graph.objects(parameter, KG.hasModifier):
            if _value(graph, modifier, KG.modifierKind) == 'facet':
                condition = _value(graph, modifier, KG.conditionName)
                if condition:
                    facets.setdefault(condition, set()).add(_key(name))
    rendered = []
    notes = [note for note in sorted(graph.objects(owner, KG.hasNote), key=str)
             if _value(graph, note, KG.noteKind) == 'facet' and _value(graph, note, KG.noteText, '')]
    # A single named facet and single prose note form an unambiguous pairing.
    if set(facets) == {facet_name} and len(notes) == 1:
        text = russian_text(_value(graph, notes[0], KG.noteText, ''))
        if not _TOKEN.search(text):
            return render_game_text(graph, owner, text, values=values)
    for note in notes:
        if _value(graph, note, KG.noteKind) != 'facet':
            continue
        text = russian_text(_value(graph, note, KG.noteText, ''))
        tokens = set()
        for match in _TOKEN.finditer(text):
            name = match.group(1) or match.group(2)
            tokens.add(_key(name[6:] if name.casefold().startswith('bonus_') else name))
        matching = {facet for facet, names in facets.items() if tokens & names}
        if matching != {facet_name}:
            continue
        display = render_game_text(graph, owner, text, values=values)
        if display and not _TOKEN.search(display):
            rendered.append(display)
    return ' '.join(dict.fromkeys(rendered)) or _fallback(graph, records, 'Значения аспекта: ')
