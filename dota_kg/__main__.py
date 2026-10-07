"""Command line entrypoint: python -m dota_kg ..."""
from __future__ import annotations

import argparse
import json

from . import opendota, stratz, valve
from .build import build


def main():
    parser = argparse.ArgumentParser(description="Current Dota 2 knowledge graph datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    collection = sub.add_parser("collect", help="Collect one source independently")
    collection.add_argument("source", choices=("valve", "opendota", "stratz"))
    sub.add_parser("build", help="Build nine curated Parquet datasets from available raw snapshots")
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build()
        elif args.source == "valve":
            result = valve.collect()
        elif args.source == "opendota":
            result = opendota.collect()
        else:
            result = stratz.discover()
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
