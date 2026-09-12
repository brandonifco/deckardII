#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_policy(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read contamination policy: {exc}") from exc

    if data.get("schemaVersion") != 1:
        raise ValueError(
            f"Unsupported contamination-policy schemaVersion: "
            f"{data.get('schemaVersion')!r}"
        )

    excluded = data.get("excludeTopLevel")
    patterns = data.get("patterns")

    if not isinstance(excluded, list) or not all(
        isinstance(item, str) and item for item in excluded
    ):
        raise ValueError("excludeTopLevel must be a list of non-empty strings")

    if not isinstance(patterns, list):
        raise ValueError("patterns must be a list")

    seen_ids: set[str] = set()

    for index, item in enumerate(patterns):
        if not isinstance(item, dict) or set(item) != {"id", "regex"}:
            raise ValueError(
                f"Pattern {index} must contain exactly id and regex"
            )

        pattern_id = item["id"]
        expression = item["regex"]

        if (
            not isinstance(pattern_id, str)
            or not pattern_id
            or not isinstance(expression, str)
            or not expression
        ):
            raise ValueError(f"Pattern {index} has invalid values")

        if pattern_id in seen_ids:
            raise ValueError(f"Duplicate pattern id: {pattern_id}")

        seen_ids.add(pattern_id)

        try:
            re.compile(expression)
        except re.error as exc:
            raise ValueError(
                f"Invalid regex for {pattern_id}: {exc}"
            ) from exc

    return data


def identity_patterns(source_prefix: str) -> list[tuple[str, re.Pattern[str]]]:
    variants = [
        ("source-prefix-exact", source_prefix),
        ("source-prefix-lower", source_prefix.lower()),
        ("source-prefix-upper", source_prefix.upper()),
    ]

    seen: set[str] = set()
    result: list[tuple[str, re.Pattern[str]]] = []

    for pattern_id, value in variants:
        if value in seen:
            continue

        seen.add(value)

        result.append(
            (
                pattern_id,
                re.compile(
                    rf"(?<![A-Za-z0-9_]){re.escape(value)}"
                    rf"(?![A-Za-z0-9_])"
                ),
            )
        )

    return result


def scan(
    root: Path,
    policy: dict,
    source_prefix: str,
    framework_name: str,
) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()

    excluded = set(policy["excludeTopLevel"])

    patterns: list[tuple[str, re.Pattern[str]]] = identity_patterns(
        source_prefix
    )

    patterns.extend(
        (
            item["id"],
            re.compile(item["regex"]),
        )
        for item in policy["patterns"]
    )

    for candidate in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        relative = candidate.relative_to(root)

        if not relative.parts:
            continue

        if relative.parts[0] in excluded:
            continue

        if candidate.is_symlink() or not candidate.is_file():
            continue

        raw = candidate.read_bytes()

        if b"\x00" in raw:
            continue

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        # Legitimate target identity is not source contamination.
        if framework_name:
            text = text.replace(framework_name, " " * len(framework_name))

        relative_text = relative.as_posix()

        for pattern_id, expression in patterns:
            count = sum(1 for _ in expression.finditer(text))

            if count:
                counts[(relative_text, pattern_id)] += count

    return counts


def baseline_document(
    policy_path: Path,
    counts: Counter[tuple[str, str]],
) -> dict:
    return {
        "schemaVersion": 1,
        "policySha256": sha256(policy_path),
        "counts": [
            {
                "path": path,
                "pattern": pattern,
                "count": count,
            }
            for (path, pattern), count in sorted(counts.items())
        ],
    }


def write_baseline(
    destination: Path,
    document: dict,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def compare_baseline(
    baseline_path: Path,
    policy_path: Path,
    actual: Counter[tuple[str, str]],
) -> int:
    try:
        baseline = json.loads(
            baseline_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"contamination: unable to read baseline: {exc}", file=sys.stderr)
        return 1

    if baseline.get("schemaVersion") != 1:
        print(
            "contamination: unsupported baseline schemaVersion",
            file=sys.stderr,
        )
        return 1

    if baseline.get("policySha256") != sha256(policy_path):
        print(
            "contamination: policy changed; baseline must be reviewed and regenerated",
            file=sys.stderr,
        )
        return 1

    expected: Counter[tuple[str, str]] = Counter()

    for item in baseline.get("counts", []):
        try:
            key = (item["path"], item["pattern"])
            count = item["count"]
        except (KeyError, TypeError):
            print("contamination: malformed baseline entry", file=sys.stderr)
            return 1

        if (
            not isinstance(key[0], str)
            or not isinstance(key[1], str)
            or not isinstance(count, int)
            or count <= 0
        ):
            print("contamination: invalid baseline entry", file=sys.stderr)
            return 1

        expected[key] += count

    if actual == expected:
        print(f"CONTAMINATION_OCCURRENCES={sum(actual.values())}")
        print(f"CONTAMINATION_BUCKETS={len(actual)}")
        print("CONTAMINATION_BASELINE=UNCHANGED")
        return 0

    keys = sorted(set(actual) | set(expected))

    print("Contamination baseline mismatch:", file=sys.stderr)

    for key in keys:
        before = expected.get(key, 0)
        now = actual.get(key, 0)

        if before == now:
            continue

        direction = "INCREASE" if now > before else "DECREASE"

        print(
            f"  {direction}: {key[0]} [{key[1]}] "
            f"baseline={before} actual={now}",
            file=sys.stderr,
        )

    return 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan framework output against a ratcheting contamination baseline."
    )

    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--source-prefix", required=True)
    parser.add_argument("--framework-name", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)

    args = parser.parse_args()

    if bool(args.baseline) == bool(args.write_baseline):
        parser.error(
            "specify exactly one of --baseline or --write-baseline"
        )

    return args


def main() -> int:
    args = parse_args()

    try:
        policy = load_policy(args.policy)
        counts = scan(
            args.root,
            policy,
            args.source_prefix,
            args.framework_name,
        )

        if args.write_baseline:
            document = baseline_document(args.policy, counts)
            write_baseline(args.write_baseline, document)

            print(f"CONTAMINATION_OCCURRENCES={sum(counts.values())}")
            print(f"CONTAMINATION_BUCKETS={len(counts)}")
            print(f"BASELINE_WRITTEN={args.write_baseline}")
            return 0

        return compare_baseline(
            args.baseline,
            args.policy,
            counts,
        )

    except (OSError, ValueError) as exc:
        print(f"contamination: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
