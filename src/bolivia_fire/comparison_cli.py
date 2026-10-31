from __future__ import annotations

import argparse
import json

from bolivia_fire.config import Paths


def run(args: argparse.Namespace, paths: Paths) -> int:
    from bolivia_fire.comparison.workflow import Comparison

    comparison = Comparison(paths)
    if args.comparison_stage == "evaluate":
        result = comparison.evaluate(
            post_hoc_test=args.post_hoc_test,
            reason=args.reason,
            reopen_reason=args.reopen_reason,
        )
    elif args.comparison_stage == "train":
        result = comparison.train(args.family)
    elif args.comparison_stage == "report":
        from bolivia_fire.comparison.report import write_report

        result = write_report(comparison.study)
    else:
        result = getattr(comparison, args.comparison_stage)()
    print(
        json.dumps(
            {
                "stage": args.comparison_stage,
                **{name: result[name] for name in ("rows", "decision", "path") if name in result},
            }
        )
    )
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    commands = subparsers.add_parser("compare").add_subparsers(
        dest="comparison_stage", required=True
    )
    for name in ("prepare", "train", "audit", "select", "evaluate", "report"):
        command = commands.add_parser(name)
        command.set_defaults(pipeline_handler=run)
        if name == "train":
            command.add_argument("--family", choices=["xgboost", "catboost", "spatial_cnn"])
        if name == "evaluate":
            command.add_argument("--post-hoc-test", action="store_true")
            command.add_argument("--reason")
            command.add_argument("--reopen-reason")
