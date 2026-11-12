"""Command line entrypoint: python -m dota_kg ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Current Dota 2 knowledge graph datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect", help="Collect one source independently")
    collection.add_argument("source", choices=("valve", "opendota"))
    sub.add_parser("build", help="Build current Parquet datasets from raw snapshots")
    for name, help_text in (("ontology", "Export game datasets into OWL/RDF and verify their values"),
                            ("validate-ontology", "Recheck the saved ontology against the game datasets")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--output", type=Path, help="Ontology directory, default: ontology/")
    query = sub.add_parser("query", help="Выполнить локальный SPARQL-запрос из файла")
    query.add_argument("file", type=Path, help="Файл .rq или .sparql")
    query.add_argument("--format", choices=("text", "table", "json", "csv", "turtle"), default="table",
                       help="Формат результата; по умолчанию таблица, для графа Turtle")
    ask = sub.add_parser("ask", help="Задать вопрос локальному графу обычным текстом")
    ask.add_argument("question", help="Например: кто контрит ПЛ на миде")
    ask.add_argument("--format", choices=("text", "json"), default="text")
    ask.add_argument("--limit", type=int, default=3, help="Вариантов на группу/вопрос, по умолчанию 3")
    query.add_argument('--bind', action='append', default=[], help='Параметр запроса: имя=значение; можно повторять')
    for command in (ask, query):
        command.add_argument('--with-item', action='append', default=[], help='Предмет у нашего героя; можно повторять')
        command.add_argument('--enemy-item', action='append', default=[], help='Предмет у противника; можно повторять')
        command.add_argument('--level', type=int, default=18, help='Уровень для оценки запаса здоровья; по умолчанию 18')
    args = parser.parse_args()
    try:
        if args.command == "ask":
            from .common import ROOT
            from .ontology import load_ontology
            from .answers import KnowledgeBase, print_answer
            if args.limit < 1:
                raise ValueError("Число вариантов должно быть положительным.")
            graph = load_ontology(ROOT / "ontology", cache=True)
            result = KnowledgeBase(graph).ask(args.question, limit=args.limit, own_items=args.with_item,
                                             enemy_items=args.enemy_item, level=args.level)
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print_answer(result, sys.stdout)
            return
        if args.command == "query":
            from .common import ROOT
            from .ontology_queries import run_query
            run_query(args.file, directory=ROOT / "ontology",
                      output_format=args.format, bindings=args.bind, own_items=args.with_item,
                      enemy_items=args.enemy_item, level=args.level)
            return
        elif args.command == "build":
            from .build import build
            result = build()
        elif args.command in ("ontology", "validate-ontology"):
            from .common import ROOT
            if args.command == "ontology":
                from .ontology import export_ontology
                report = export_ontology(output=args.output)
            else:
                from .ontology_validation import validate_export
                report = validate_export(ROOT, args.output or ROOT / "ontology")
                if not report["conforms"]:
                    raise RuntimeError(f"Ontology validation failed: {report['errors']}")
            result = {key: report[key] for key in ("conforms", "triples", "source_tables",
                                                   "cells_checked", "class_counts")}
            result["report"] = str((args.output or ROOT / "ontology") / "validation.json")
        elif args.source == "valve":
            from . import valve
            result = valve.collect()
        elif args.source == "opendota":
            from . import opendota
            result = opendota.collect()
    except ModuleNotFoundError as error:
        parser.exit(1, f"В этом Python нет зависимости {error.name}. Запустите команду через .\\.venv\\Scripts\\python.exe или активируйте .venv.\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nВыполнение отменено.\n")
    except (RuntimeError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
