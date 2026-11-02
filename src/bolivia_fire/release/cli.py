from __future__ import annotations

import argparse
import json
from pathlib import Path

from bolivia_fire.config import Paths
from bolivia_fire.release.build import (
    inputs_directory,
    package_release,
    stage_release,
    validate_version,
)


def register(commands: argparse._SubParsersAction) -> None:
    operations = commands.add_parser("release").add_subparsers(dest="operation", required=True)
    for name in ("stage", "package", "verify"):
        parser = operations.add_parser(name)
        parser.add_argument("--version", required=True)
        if name in {"stage", "package"}:
            parser.add_argument("--inputs-dir", type=Path,
                                help="Release inputs; BOLIVIA_FIRE_RELEASE_INPUTS fallback")
        if name == "stage":
            parser.add_argument("--audit-dir", type=Path)
        elif name == "package":
            parser.add_argument("--docs-dir", type=Path)
        else:
            parser.add_argument("--extract-to", type=Path, required=True)
            parser.add_argument("--internal-notes-terms", type=Path,
                                help="External regex file; BOLIVIA_FIRE_RELEASE_TERMS fallback")
        parser.set_defaults(pipeline_handler=run)


def run(args: argparse.Namespace, paths: Paths) -> int:
    validate_version(args.version)
    stage = paths.root / "datasets/releases" / args.version
    if args.operation == "stage":
        audit = args.audit_dir
        if audit is None:
            audit = inputs_directory(args.inputs_dir) / "RA1"
        result = stage_release(paths.root, stage, args.version, audit)
    elif args.operation == "package":
        docs = args.docs_dir
        if docs is None:
            docs = inputs_directory(args.inputs_dir) / "archive_docs"
        result = package_release(paths.root, stage, docs=docs)
    else:
        from bolivia_fire.release.security import load_internal_notes
        from bolivia_fire.release.verify import verify_release

        terms = load_internal_notes(args.internal_notes_terms)
        result = verify_release(paths.root, stage, args.extract_to, internal_notes=terms)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("passed", True) else 1
