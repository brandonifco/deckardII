#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SUPPORTED_PLACEHOLDERS = {
    "{{SOURCE_PREFIX}}",
    "{{SOURCE_PREFIX_UPPER}}",
    "{{SOURCE_PREFIX_LOWER}}",
    "{{FRAMEWORK_PREFIX}}",
    "{{FRAMEWORK_PREFIX_UPPER}}",
    "{{FRAMEWORK_PREFIX_LOWER}}",
    "{{FRAMEWORK_ID}}",
    "{{FRAMEWORK_NAME}}",
    "{{SOURCE_REPO}}",
    "{{TARGET_REPO}}",
}


def build_values(args: argparse.Namespace) -> dict[str, str]:
    return {
        "{{SOURCE_PREFIX}}": args.source_prefix,
        "{{SOURCE_PREFIX_UPPER}}": args.source_prefix.upper(),
        "{{SOURCE_PREFIX_LOWER}}": args.source_prefix.lower(),
        "{{FRAMEWORK_PREFIX}}": args.framework_prefix,
        "{{FRAMEWORK_PREFIX_UPPER}}": args.framework_prefix.upper(),
        "{{FRAMEWORK_PREFIX_LOWER}}": args.framework_prefix.lower(),
        "{{FRAMEWORK_ID}}": args.framework_id,
        "{{FRAMEWORK_NAME}}": args.framework_name,
        "{{SOURCE_REPO}}": args.source_repo,
        "{{TARGET_REPO}}": args.target_repo,
    }


def substitute_placeholders(value: str, values: dict[str, str]) -> str:
    rendered = value

    for placeholder, replacement in values.items():
        rendered = rendered.replace(placeholder, replacement)

    for placeholder in SUPPORTED_PLACEHOLDERS:
        if placeholder in rendered:
            raise ValueError(f"Unresolved placeholder: {placeholder}")

    if "{{" in rendered or "}}" in rendered:
        raise ValueError(f"Unknown placeholder in rename rule: {rendered}")

    return rendered


def load_map(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read rename map {path}: {exc}") from exc

    if data.get("schemaVersion") != 1:
        raise ValueError(
            f"Unsupported rename-map schemaVersion: {data.get('schemaVersion')!r}"
        )

    rules = data.get("rules")

    if not isinstance(rules, list):
        raise ValueError("rename-map rules must be a list")

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} is not an object")

        required = {"path", "search", "replace"}

        if set(rule) != required:
            raise ValueError(
                f"Rule {index} must contain exactly: path, search, replace"
            )

        for key in required:
            if not isinstance(rule[key], str) or not rule[key]:
                raise ValueError(f"Rule {index} has invalid {key!r}")

        candidate = Path(rule["path"])

        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.parts[:1] == (".git",)
        ):
            raise ValueError(f"Rule {index} has unsafe path: {rule['path']}")

    return data


def managed_paths(data: dict) -> list[str]:
    return sorted({rule["path"] for rule in data["rules"]})


def render(
    data: dict,
    relative_path: str,
    source_text: str,
    values: dict[str, str],
) -> tuple[str, int]:
    result = source_text
    applied = 0

    for index, rule in enumerate(data["rules"]):
        if rule["path"] != relative_path:
            continue

        search = substitute_placeholders(rule["search"], values)
        replacement = substitute_placeholders(rule["replace"], values)

        count = result.count(search)

        if count == 0:
            raise ValueError(
                f"Rule {index} for {relative_path} matched zero occurrences: "
                f"{search!r}"
            )

        result = result.replace(search, replacement)
        applied += count

    return result, applied


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render one file through the declared framework rename map."
    )

    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--path", dest="relative_path")
    parser.add_argument("--input", type=Path)

    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--framework-prefix", required=True)
    parser.add_argument("--framework-id", required=True)
    parser.add_argument("--framework-name", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--target-repo", required=True)

    parser.add_argument(
        "--list-managed-paths",
        action="store_true",
        help="Print paths governed by at least one rename rule.",
    )

    args = parser.parse_args()

    if not args.list_managed_paths:
        if not args.relative_path:
            parser.error("--path is required unless --list-managed-paths is used")

        if not args.input:
            parser.error("--input is required unless --list-managed-paths is used")

    return args


def main() -> int:
    args = parse_args()

    try:
        data = load_map(args.map)
        values = build_values(args)

        if args.list_managed_paths:
            for path in managed_paths(data):
                print(path)
            return 0

        source_bytes = args.input.read_bytes()

        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"Transform-managed file is not UTF-8: {args.input}"
            ) from exc

        rendered, applied = render(
            data,
            args.relative_path,
            source_text,
            values,
        )

        if applied == 0:
            raise ValueError(
                f"No rename rules declared for path: {args.relative_path}"
            )

        sys.stdout.buffer.write(rendered.encode("utf-8"))
        return 0

    except (OSError, ValueError) as exc:
        print(f"rename-engine: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
