from __future__ import annotations

import argparse
import json

from bolivia_fire.config import Paths


def run(args: argparse.Namespace, paths: Paths) -> int:
    from bolivia_fire.extension.registration import verify_registration

    if args.extension_stage == "guard":
        registration = verify_registration(paths)
        print(
            json.dumps(
                {
                    "status": "registered",
                    "planned_runs": len(registration.runs),
                    **registration.provenance(),
                }
            )
        )
        return 0
    from bolivia_fire.extension.runner import Extension

    experiment = Extension(paths, args.inputs)
    try:
        if args.extension_stage == "select":
            result = experiment.select()
        else:
            confirmation = args.extension_stage == "confirm"
            result = experiment.score(
                scope="confirmation" if confirmation else "test",
                explicit=args.open_confirmation if confirmation else args.open_test,
                reason=args.reason if confirmation else args.reopen_reason,
                confirmation_evidence=args.evidence if confirmation else None,
            )
        print(
            json.dumps(
                {
                    "stage": args.extension_stage,
                    "hypotheses": result.get("hypotheses"),
                    "runs": len(result["runs"]),
                }
            )
        )
    finally:
        experiment.close()
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    commands = subparsers.add_parser("extension").add_subparsers(
        dest="extension_stage",
        required=True,
    )
    for name in ("guard", "select", "score-test", "confirm"):
        parser = commands.add_parser(name)
        parser.set_defaults(pipeline_handler=run)
        if name != "guard":
            parser.add_argument("--inputs", required=True, help="Frozen label-free input plan JSON")
        if name == "score-test":
            parser.add_argument("--open-test", action="store_true")
            parser.add_argument("--reopen-reason")
        if name == "confirm":
            parser.add_argument("--open-confirmation", action="store_true")
            parser.add_argument("--reason")
            parser.add_argument(
                "--evidence", required=True, help="Frozen confirmation evidence JSON"
            )
