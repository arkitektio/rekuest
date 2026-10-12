"""The ``kulisse`` command: import, check, joints, snippet."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

from . import check, snippet
from .urdf import UrdfImportError, import_urdf, with_joints
from .validate import ModelPolicy, ModelRefused, model_ref


def _pairs(values: list[str], separator: str, what: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for value in values:
        name, found, rest = value.partition(separator)
        if not found or not name:
            raise SystemExit(f"{what} is written name{separator}value, not {value!r}")
        pairs[name] = rest
    return pairs


def _policy(path: str | None) -> ModelPolicy | None:
    """A policy read from a JSON file, as ``modelPolicy`` returns it (camelCase or snake_case keys)."""
    if path is None:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    known = {field.name for field in fields(ModelPolicy)}
    values: dict[str, object] = {}
    for key, value in raw.items():
        name = "".join("_" + letter.lower() if letter.isupper() else letter for letter in key)
        if name not in known:
            raise SystemExit(f"{path}: unknown policy field {key!r}")
        values[name] = frozenset(value) if name == "extensions" else int(value)
    return ModelPolicy(**values)  # type: ignore[arg-type]


def _report(path: Path, policy: ModelPolicy | None) -> None:
    report = check(path, policy)
    print(f"{path}  {report.ref}")
    stats = report.stats
    print(f"  {report.size} bytes, {stats.nodes} nodes, {stats.triangles} triangles, {stats.textures} textures, {stats.animations} clips, {len(report.joints)} joints")
    for warning in report.warnings:
        print(f"  warning: {warning}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kulisse", description="Author device models for Arkitekt's 3D view.")
    commands = parser.add_subparsers(dest="command", required=True)

    importer = commands.add_parser("import", help="convert a URDF or Xacro file into a GLB model")
    importer.add_argument("urdf", type=Path)
    importer.add_argument("--out", type=Path, required=True, help="the GLB file to write")
    importer.add_argument("--package", action="append", default=[], metavar="NAME=DIR", help="where a package:// the file refers to is on disk")
    importer.add_argument("--arg", action="append", default=[], metavar="NAME:=VALUE", help="a Xacro argument")

    checker = commands.add_parser("check", help="hold a GLB to the rules a model is held to")
    checker.add_argument("glb", type=Path)
    checker.add_argument("--policy", help="a JSON file with an organization's policy; the deployment defaults otherwise")

    jointer = commands.add_parser("joints", help="write a joint table into a GLB that was not imported from URDF")
    jointer.add_argument("glb", type=Path)
    jointer.add_argument("table", type=Path, help="a JSON file: a list of joints as extras.arkitekt.joints holds them")
    jointer.add_argument("--out", type=Path, required=True)

    snipper = commands.add_parser("snippet", help="print the blok component that references a model")
    snipper.add_argument("glb", type=Path)
    snipper.add_argument("--name", default=None, help="the name the app registers the file under; the file's name without its extension by default")

    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            data = import_urdf(args.urdf, packages=_pairs(args.package, "=", "--package"), arguments=_pairs(args.arg, ":=", "--arg"))
            args.out.write_bytes(data)
            _report(args.out, None)
            print(f"wrote {args.out}  {model_ref(data)}")
        elif args.command == "check":
            _report(args.glb, _policy(args.policy))
        elif args.command == "joints":
            data = with_joints(args.glb.read_bytes(), json.loads(args.table.read_text(encoding="utf-8")))
            args.out.write_bytes(data)
            _report(args.out, None)
            print(f"wrote {args.out}  {model_ref(data)}")
        else:
            print(snippet(args.glb, args.name))
    except ModelRefused as refusal:
        print(f"refused ({refusal.code}): {refusal.message}", file=sys.stderr)
        return 1
    except (UrdfImportError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
