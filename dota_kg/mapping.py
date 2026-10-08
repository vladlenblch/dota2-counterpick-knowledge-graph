"""Canonical Valve identity resolution with explicit ambiguity reporting."""
from __future__ import annotations

import re
import unicodedata


def _display_key(value):
    text = unicodedata.normalize("NFKD", value or "").casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


class Mapper:
    def __init__(self, heroes, items):
        self.entities = {"hero": heroes, "item": items}
        self.problems = []
        self._reported = set()
        self.indexes = {}
        for kind, rows in self.entities.items():
            identifier = f"{kind}_id"
            indexes = [{}, {}, {}]
            for row in rows:
                for index, key in enumerate((row.get(identifier), row.get("internal_name"), _display_key(row.get("display_name")))):
                    if key is not None:
                        indexes[index].setdefault(key, set()).add(row[identifier])
            self.indexes[kind] = indexes

    def resolve(self, kind, *, numeric_id=None, internal_name=None, display_name=None, source=None):
        indexes = self.indexes[kind]
        candidates = [("numeric", numeric_id), ("internal", internal_name), ("display", _display_key(display_name))]
        for index, (method, key) in enumerate(candidates):
            if key is None or key == "":
                continue
            matches = indexes[index].get(key, set())
            if len(matches) == 1:
                return next(iter(matches))
            if len(matches) > 1:
                self._problem({"source": source, "kind": kind, "method": method, "key": key, "candidates": sorted(matches)})
                return None
        self._problem({"source": source, "kind": kind, "method": "unresolved", "numeric_id": numeric_id, "internal_name": internal_name, "display_name": display_name})
        return None

    def _problem(self, problem):
        key = repr(sorted(problem.items()))
        if key not in self._reported:
            self.problems.append(problem)
            self._reported.add(key)
