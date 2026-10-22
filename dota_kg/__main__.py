"""Command line entrypoint: python -m dota_kg ..."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import opendota, valve
from .build import build


def main():
    parser = argparse.ArgumentParser(description="Current Dota 2 knowledge graph datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect", help="Collect one source independently")
    collection.add_argument("source", choices=("valve", "opendota"))
    sub.add_parser("build", help="Build current Parquet datasets from raw snapshots")
    for name, help_text in (("ontology", "Export all data into OWL/RDF and verify every source value"),
                            ("validate-ontology", "Recheck the saved ontology against every source file")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--output", type=Path, help="Ontology directory, default: ontology/")
    query = sub.add_parser("query", help="Выполнить локальный SPARQL-запрос из файла")
    query.add_argument("file", type=Path, help="Файл .rq или .sparql")
    query.add_argument("--full", action="store_true", help="Подключить полный архив исходных данных")
    query.add_argument("--format", choices=("table", "json", "csv", "turtle"), default="table",
                       help="Формат результата; по умолчанию таблица, для графа Turtle")
    args = parser.parse_args()
    try:
        if args.command == "query":
            from .common import ROOT
            from .ontology_queries import run_query
            run_query(args.file, directory=ROOT / "ontology", include_archive=args.full,
                      output_format=args.format)
            return
        elif args.command == "build":
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
            result = {key: report[key] for key in ("conforms", "triples", "source_files",
                                                   "cells_verified", "class_counts")}
            result["report"] = str((args.output or ROOT / "ontology") / "validation.json")
        elif args.source == "valve":
            result = valve.collect()
        elif args.source == "opendota":
            result = opendota.collect()
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
