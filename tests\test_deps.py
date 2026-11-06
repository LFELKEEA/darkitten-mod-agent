from __future__ import annotations

import unittest
from pathlib import Path

from dma import deps

from .base import TempDataCase


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class JarReferenceTests(TempDataCase):
    def test_reads_quoted_libs_paths(self) -> None:
        root = self.tmp / "project"
        write(
            root / "build.gradle",
            "\n".join(
                [
                    "dependencies {",
                    "    compileOnly files('libs/mca-neoforge-7.7.36-beta.3+1.21.1.jar')",
                    '    implementation files("libs/helper.jar")',
                    "    // implementation files('libs/old.jar')",
                    "    implementation 'com.example:other:1.0'",
                    "}",
                ]
            ),
        )
        refs = deps.jar_references(root)
        self.assertEqual(
            refs,
            ["libs/mca-neoforge-7.7.36-beta.3+1.21.1.jar", "libs/helper.jar"],
        )

    def test_kotlin_and_duplicates(self) -> None:
        root = self.tmp / "project"
        write(
            root / "build.gradle.kts",
            'dependencies {\n    compileOnly(files("libs/a.jar"))\n'
            '    compileOnly(files("libs/a.jar"))\n}\n',
        )
        self.assertEqual(deps.jar_references(root), ["libs/a.jar"])

    def test_ignores_paths_outside_project(self) -> None:
        root = self.tmp / "project"
        write(root / "build.gradle", "dependencies { compileOnly files('../libs/a.jar') }\n")
        self.assertEqual(deps.jar_references(root), [])


class MatchScoreTests(unittest.TestCase):
    def test_exact_and_prefixed_names(self) -> None:
        expected = "mca-neoforge-7.7.36-beta.3+1.21.1.jar"
        self.assertGreater(deps.match_score(expected, expected), 500)
        self.assertGreater(
            deps.match_score(expected, "[凡家物语：重生] " + expected), 500
        )
        self.assertEqual(deps.match_score(expected, "other-mod-1.0.jar"), 0)

    def test_sources_jar_is_not_a_match(self) -> None:
        expected = "mca-neoforge-7.7.36-beta.3+1.21.1.jar"
        self.assertEqual(
            deps.match_score(expected, "mca-neoforge-7.7.36-beta.3+1.21.1-sources.jar"),
            0,
        )

    def test_error_text(self) -> None:
        text = (
            "Could not find files for the given pattern(s).\n"
            "  > libs/mca-neoforge-7.7.36-beta.3+1.21.1.jar"
        )
        self.assertTrue(deps.is_missing_jar_error(text))
        self.assertEqual(
            deps.mentioned_jar(text), "mca-neoforge-7.7.36-beta.3+1.21.1.jar"
        )
        self.assertTrue(
            deps.is_missing_jar_error("Could not find files for the given pattern(s).")
        )
        self.assertFalse(deps.is_missing_jar_error("Build failed with an exception."))


class EnsureLocalJarsTests(TempDataCase):
    def test_copies_jar_from_extra_dir(self) -> None:
        root = self.tmp / "project"
        downloads = self.tmp / "downloads"
        name = "mca-neoforge-7.7.36-beta.3+1.21.1.jar"
        write(root / "build.gradle", f"dependencies {{ compileOnly files('libs/{name}') }}\n")
        source = downloads / ("[凡家物语：重生] " + name)
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"jar-bytes")

        result = deps.ensure_local_jars(root, extra_dirs=[downloads])
        self.assertEqual(len(result.copied), 1)
        self.assertEqual(result.copied[0][0], f"libs/{name}")
        self.assertTrue((root / "libs" / name).is_file())
        self.assertEqual((root / "libs" / name).read_bytes(), b"jar-bytes")

        again = deps.ensure_local_jars(root, extra_dirs=[downloads])
        self.assertEqual(again.copied, [])
        self.assertEqual(again.present, [f"libs/{name}"])

    def test_reports_missing_jar(self) -> None:
        root = self.tmp / "project"
        name = "absent-1.0.jar"
        write(root / "build.gradle", f"dependencies {{ compileOnly files('libs/{name}') }}\n")
        result = deps.ensure_local_jars(root, extra_dirs=[])
        self.assertEqual(result.missing, [f"libs/{name}"])
        self.assertIn("没找到", result.summary())


if __name__ == "__main__":
    unittest.main()
