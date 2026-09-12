#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import rename_engine


@dataclass(frozen=True)
class PathRule:
    source: str
    target: str


def _validate_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")

    if "\\" in value:
        raise ValueError(
            f"{label} must use repository-relative POSIX separators: {value!r}"
        )

    parts = value.split("/")

    if (
        value.startswith("/")
        or value.endswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or parts[0] == ".git"
    ):
        raise ValueError(f"{label} is unsafe: {value!r}")

    return value


def load_map(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read path map {path}: {exc}") from exc

    if data.get("schemaVersion") != 1:
        raise ValueError(
            f"Unsupported path-map schemaVersion: {data.get('schemaVersion')!r}"
        )

    rules = data.get("rules")

    if not isinstance(rules, list):
        raise ValueError("path-map rules must be a list")

    seen_sources: set[str] = set()
    seen_targets: set[str] = set()

    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} is not an object")

        if set(rule) != {"source", "target"}:
            raise ValueError(
                f"Rule {index} must contain exactly: source, target"
            )

        source = _validate_path(
            rule["source"],
            label=f"Rule {index} source",
        )
        target = _validate_path(
            rule["target"],
            label=f"Rule {index} target",
        )

        if source in seen_sources:
            raise ValueError(
                f"Rule {index} duplicates source path template: {source}"
            )

        if target in seen_targets:
            raise ValueError(
                f"Rule {index} duplicates target path template: {target}"
            )

        seen_sources.add(source)
        seen_targets.add(target)

    return data


def render_rules(
    data: dict,
    values: dict[str, str],
) -> list[PathRule]:
    rendered: list[PathRule] = []
    seen_sources: set[str] = set()
    seen_targets: set[str] = set()

    for index, rule in enumerate(data["rules"]):
        source = rename_engine.substitute_placeholders(
            rule["source"],
            values,
        )
        target = rename_engine.substitute_placeholders(
            rule["target"],
            values,
        )

        source = _validate_path(
            source,
            label=f"Rendered rule {index} source",
        )
        target = _validate_path(
            target,
            label=f"Rendered rule {index} target",
        )

        if source in seen_sources:
            raise ValueError(
                f"Rendered rule {index} duplicates source path: {source}"
            )

        if target in seen_targets:
            raise ValueError(
                f"Rendered rule {index} duplicates target path: {target}"
            )

        seen_sources.add(source)
        seen_targets.add(target)
        rendered.append(PathRule(source=source, target=target))

    return rendered


def _is_within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def map_path(
    relative_path: str,
    rules: list[PathRule],
) -> tuple[str, PathRule | None]:
    relative_path = _validate_path(
        relative_path,
        label="Source repository path",
    )

    matches = [
        rule
        for rule in rules
        if _is_within(relative_path, rule.source)
    ]

    if not matches:
        return relative_path, None

    rule = max(
        matches,
        key=lambda candidate: len(candidate.source.split("/")),
    )

    if relative_path == rule.source:
        return rule.target, rule

    suffix = relative_path[len(rule.source) + 1 :]
    mapped = f"{rule.target}/{suffix}"

    return (
        _validate_path(
            mapped,
            label="Mapped target repository path",
        ),
        rule,
    )


def map_paths(
    relative_paths: list[str],
    rules: list[PathRule],
) -> dict[str, str]:
    result: dict[str, str] = {}
    target_owners: dict[str, str] = {}

    for source in relative_paths:
        target, _rule = map_path(source, rules)
        prior = target_owners.get(target)

        if prior is not None and prior != source:
            raise ValueError(
                f"Path-map collision: {prior!r} and {source!r} "
                f"both map to {target!r}"
            )

        target_owners[target] = source
        result[source] = target

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Map a source repository path through the declared "
            "framework path map."
        )
    )

    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--path", dest="relative_path")

    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--framework-prefix", required=True)
    parser.add_argument("--framework-id", required=True)
    parser.add_argument("--framework-name", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--target-repo", required=True)

    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="Print rendered source-to-target path rules.",
    )

    args = parser.parse_args()

    if not args.list_rules and not args.relative_path:
        parser.error("--path is required unless --list-rules is used")

    return args


def main() -> int:
    args = parse_args()

    try:
        data = load_map(args.map)
        values = rename_engine.build_values(args)
        rules = render_rules(data, values)

        if args.list_rules:
            for rule in rules:
                print(f"{rule.source}\t{rule.target}")
            return 0

        mapped, _rule = map_path(args.relative_path, rules)
        print(mapped)
        return 0

    except (OSError, ValueError) as exc:
        print(f"path-map-engine: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
