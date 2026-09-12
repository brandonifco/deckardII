"""Tests for bootstrap/lib/path_map_engine.py."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "bootstrap" / "lib"
sys.path.insert(0, str(LIB))

import path_map_engine  # noqa: E402


VALUES = {
    "{{SOURCE_PREFIX}}": "Source",
    "{{SOURCE_PREFIX_UPPER}}": "SOURCE",
    "{{SOURCE_PREFIX_LOWER}}": "source",
    "{{FRAMEWORK_PREFIX}}": "Framework",
    "{{FRAMEWORK_PREFIX_UPPER}}": "FRAMEWORK",
    "{{FRAMEWORK_PREFIX_LOWER}}": "framework",
    "{{FRAMEWORK_ID}}": "framework",
    "{{FRAMEWORK_NAME}}": "Framework Engine",
    "{{SOURCE_REPO}}": "example/source",
    "{{TARGET_REPO}}": "example/framework",
}


def document(*rules: tuple[str, str]) -> dict:
    return {
        "schemaVersion": 1,
        "rules": [
            {"source": source, "target": target}
            for source, target in rules
        ],
    }


class LoadMapTests(unittest.TestCase):
    def write_map(self, payload: object) -> Path:
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        )
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        json.dump(payload, handle)
        handle.write("\n")
        handle.close()
        return path

    def test_valid_map_loads(self):
        path = self.write_map(
            document(
                (
                    "src/{{SOURCE_PREFIX}}.Core",
                    "src/{{FRAMEWORK_PREFIX}}.Core",
                ),
            )
        )

        loaded = path_map_engine.load_map(path)

        self.assertEqual(loaded["schemaVersion"], 1)
        self.assertEqual(len(loaded["rules"]), 1)

    def test_unsupported_schema_is_rejected(self):
        path = self.write_map(
            {
                "schemaVersion": 2,
                "rules": [],
            }
        )

        with self.assertRaisesRegex(ValueError, "schemaVersion"):
            path_map_engine.load_map(path)

    def test_rule_shape_is_exact(self):
        path = self.write_map(
            {
                "schemaVersion": 1,
                "rules": [
                    {
                        "source": "src/A",
                        "target": "src/B",
                        "extra": "no",
                    }
                ],
            }
        )

        with self.assertRaisesRegex(ValueError, "exactly"):
            path_map_engine.load_map(path)

    def test_unsafe_paths_are_rejected(self):
        for unsafe in (
            "/absolute",
            "../escape",
            "src/../escape",
            ".git/config",
            "src//double",
            "src/trailing/",
            r"src\windows",
        ):
            with self.subTest(unsafe=unsafe):
                path = self.write_map(
                    document((unsafe, "safe/target"))
                )

                with self.assertRaises(ValueError):
                    path_map_engine.load_map(path)

    def test_duplicate_source_templates_are_rejected(self):
        path = self.write_map(
            document(
                ("src/A", "src/B"),
                ("src/A", "src/C"),
            )
        )

        with self.assertRaisesRegex(ValueError, "duplicates source"):
            path_map_engine.load_map(path)

    def test_duplicate_target_templates_are_rejected(self):
        path = self.write_map(
            document(
                ("src/A", "src/C"),
                ("src/B", "src/C"),
            )
        )

        with self.assertRaisesRegex(ValueError, "duplicates target"):
            path_map_engine.load_map(path)


class RenderAndMappingTests(unittest.TestCase):
    def test_placeholders_render_in_both_sides(self):
        rules = path_map_engine.render_rules(
            document(
                (
                    "src/{{SOURCE_PREFIX}}.Core",
                    "src/{{FRAMEWORK_PREFIX}}.Core",
                ),
            ),
            VALUES,
        )

        self.assertEqual(
            rules,
            [
                path_map_engine.PathRule(
                    source="src/Source.Core",
                    target="src/Framework.Core",
                )
            ],
        )

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown placeholder"):
            path_map_engine.render_rules(
                document(
                    ("src/{{NOT_SUPPORTED}}", "src/Framework"),
                ),
                VALUES,
            )

    def test_directory_rule_preserves_suffix(self):
        rules = [
            path_map_engine.PathRule(
                "src/Source.Core",
                "src/Framework.Core",
            )
        ]

        mapped, matched = path_map_engine.map_path(
            "src/Source.Core/Randomness/Pcg32.cs",
            rules,
        )

        self.assertEqual(
            mapped,
            "src/Framework.Core/Randomness/Pcg32.cs",
        )
        self.assertEqual(matched, rules[0])

    def test_exact_file_rule_beats_parent_directory_rule(self):
        directory = path_map_engine.PathRule(
            "src/Source.Core",
            "src/Framework.Core",
        )
        project = path_map_engine.PathRule(
            "src/Source.Core/Source.Core.csproj",
            "src/Framework.Core/Framework.Core.csproj",
        )

        mapped, matched = path_map_engine.map_path(
            "src/Source.Core/Source.Core.csproj",
            [directory, project],
        )

        self.assertEqual(
            mapped,
            "src/Framework.Core/Framework.Core.csproj",
        )
        self.assertEqual(matched, project)

    def test_prefix_match_is_component_bounded(self):
        rules = [
            path_map_engine.PathRule(
                "src/Source.Core",
                "src/Framework.Core",
            )
        ]

        mapped, matched = path_map_engine.map_path(
            "src/Source.CoreExtra/File.cs",
            rules,
        )

        self.assertEqual(
            mapped,
            "src/Source.CoreExtra/File.cs",
        )
        self.assertIsNone(matched)

    def test_unmapped_path_is_unchanged(self):
        mapped, matched = path_map_engine.map_path(
            "Directory.Build.props",
            [],
        )

        self.assertEqual(mapped, "Directory.Build.props")
        self.assertIsNone(matched)

    def test_collisions_are_rejected(self):
        rules = [
            path_map_engine.PathRule(
                "source/A.txt",
                "source/B.txt",
            )
        ]

        with self.assertRaisesRegex(ValueError, "collision"):
            path_map_engine.map_paths(
                ["source/A.txt", "source/B.txt"],
                rules,
            )

    def test_nested_rules_choose_most_specific_source(self):
        rules = [
            path_map_engine.PathRule(
                "tests/Source.Core.Tests",
                "tests/Framework.Core.Tests",
            ),
            path_map_engine.PathRule(
                "tests/Source.Core.Tests/Source.Core.Tests.csproj",
                "tests/Framework.Core.Tests/Framework.Core.Tests.csproj",
            ),
        ]

        mapped, matched = path_map_engine.map_path(
            "tests/Source.Core.Tests/Source.Core.Tests.csproj",
            rules,
        )

        self.assertEqual(
            mapped,
            "tests/Framework.Core.Tests/Framework.Core.Tests.csproj",
        )
        self.assertIsNotNone(matched)
        self.assertEqual(
            matched.source,
            "tests/Source.Core.Tests/Source.Core.Tests.csproj",
        )


if __name__ == "__main__":
    unittest.main()
