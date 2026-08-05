import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .dataset import (adjudicate, assert_frozen_test_writable, dataset_summary,
                      export_annotation_template, import_annotations,
                      split_manifest, validate_cases)


def read_cases(path: Path):
    return validate_cases(json.loads(path.read_text("utf-8")))


def write_json(path: Path, value):
    assert_frozen_test_writable(path, None)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", "utf-8")


def main():
    parser = argparse.ArgumentParser(prog="python -m research_evaluation.dataset_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "summary", "manifest"):
        cmd = sub.add_parser(name); cmd.add_argument("input", type=Path)
        cmd.add_argument("--output", type=Path)
    export = sub.add_parser("export"); export.add_argument("input", type=Path); export.add_argument("output", type=Path)
    imp = sub.add_parser("import"); imp.add_argument("input", type=Path); imp.add_argument("package", type=Path); imp.add_argument("output", type=Path); imp.add_argument("--annotator", required=True)
    adj = sub.add_parser("adjudicate"); adj.add_argument("input", type=Path); adj.add_argument("output", type=Path); adj.add_argument("--case-id", required=True); adj.add_argument("--label", required=True); adj.add_argument("--adjudicator", required=True); adj.add_argument("--rationale", required=True)
    args = parser.parse_args(); cases = read_cases(args.input)
    if args.command == "validate": result = {"valid": True, "case_count": len(cases)}
    elif args.command == "summary": result = dataset_summary(cases)
    elif args.command == "manifest": result = split_manifest(cases)
    elif args.command == "export": write_json(args.output, export_annotation_template(cases)); return
    elif args.command == "import":
        package = json.loads(args.package.read_text("utf-8"))
        result = [c.model_dump(mode="json") for c in import_annotations(cases, package, args.annotator, datetime.now(timezone.utc))]
    else:
        result = [adjudicate(c, args.label, args.adjudicator, args.rationale, datetime.now(timezone.utc)).model_dump(mode="json") if c.case_id == args.case_id else c.model_dump(mode="json") for c in cases]
    if getattr(args, "output", None): write_json(args.output, result)
    else: print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__": main()
