from __future__ import annotations

import argparse
import json

from bolivia_fire.config import Paths


def run(args: argparse.Namespace, paths: Paths) -> int:
    from bolivia_fire.vegetation_features.pipeline import build

    result = build(paths.root, through_year=args.through_year)
    print(json.dumps({k: v for k, v in result.items() if "coverage" not in k}))
    return 0


def register(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("vegetation-features")
    parser.add_argument("--through-year", type=int)
    parser.set_defaults(pipeline_handler=run)
