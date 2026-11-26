"""Command line entrypoint: python -m dota_kg ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Граф знаний Dota 2")
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect", help="Собрать данные выбранного источника")
    collection.add_argument("source", choices=("valve", "opendota"))
    sub.add_parser("build", help="Собрать датасеты Parquet из сохраненных ответов API")
    for name, help_text in (("ontology", "Выгрузить игровые данные в OWL/RDF и проверить значения"),
                            ("validate-ontology", "Проверить сохраненную онтологию по датасетам")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--output", type=Path, help="Папка онтологии, по умолчанию ontology/")
    query = sub.add_parser("query", help="Выполнить локальный SPARQL-запрос из файла")
    query.add_argument("file", type=Path, help="Файл .rq или .sparql")
    query.add_argument("--format", choices=("text", "json", "csv", "turtle"), default="text",
                       help="Формат результата; по умолчанию текст без таблиц, для графа Turtle")
    ask = sub.add_parser("ask", help="Задать вопрос локальному графу обычным текстом")
    ask.add_argument("question", help="Например: кто контрит ПЛ на миде")
    ask.add_argument("--format", choices=("text", "json"), default="text")
    ask.add_argument("--limit", type=int, default=3, help="Вариантов на группу/вопрос, по умолчанию 3")
    rank = sub.add_parser("rank", help="Общий топ контрпиков по взвешенным игровым механикам")
    predict = sub.add_parser("predict", help="Показать сохранённые гипотезы ComplEx о матчапах")
    for command in (rank, predict):
        command.add_argument("opponent", help="Имя противника, например ВР или Windranger")
        command.add_argument("--limit", type=int, default=5, help="Количество кандидатов")
    query.add_argument('--bind', action='append', default=[], help='Параметр запроса: имя=значение; можно повторять')
    for command in (ask, query):
        command.add_argument('--with-item', action='append', default=[], help='Предмет у нашего героя; можно повторять')
        command.add_argument('--enemy-item', action='append', default=[], help='Предмет у противника; можно повторять')
        command.add_argument('--level', type=int, default=18, help='Уровень для оценки запаса здоровья; по умолчанию 18')
    args = parser.parse_args()
    try:
        if args.command in {"rank", "predict"}:
            from rdflib import RDFS
            from .common import ROOT
            from .ontology import load_ontology
            from .ontology_schema import KG
            from .questions import QuestionParser

            if args.limit < 1:
                raise ValueError("Число вариантов должно быть положительным.")
            graph = load_ontology(ROOT / "ontology", cache=True)
            opponent = QuestionParser(graph).resolve_entity(args.opponent, KG.Hero)
            if args.command == "predict":
                from .embeddings import read_predictions

                predictions = read_predictions(opponent)
                if not predictions:
                    print(f"Для {graph.value(opponent, RDFS.label)} нет сохранённых гипотез о матчапах.")
                for hero, score in predictions[:args.limit]:
                    print(f"{graph.value(hero, RDFS.label)} — прогноз винрейта выше среднего (балл ComplEx {score:+.3f})")
                return
            from .ranking import RankingModel

            results = RankingModel(graph, use_statistics=False).rank(opponent)
            for position, result in enumerate(results[:args.limit], start=1):
                name = graph.value(result.hero, RDFS.label)
                ability = graph.value(result.primary.fact.owner, RDFS.label)
                print(f"{position}. {name} — {ability}: {result.score:.3f}")
            return
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
                    raise RuntimeError(f"Онтология не прошла проверку: {report['errors']}")
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
    except (RuntimeError, ValueError, FileNotFoundError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
