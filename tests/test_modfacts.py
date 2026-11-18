from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dma import archref, modfacts, multiversion
from dma.kernel.tools import crash
from dma import skills, tool_schema


class VersionFactsTest(unittest.TestCase):
    def test_version_tuple_and_family(self) -> None:
        self.assertEqual(modfacts.version_tuple("1.21.1"), (1, 21, 1))
        self.assertEqual(modfacts.version_tuple("26.1.2"), (26, 1, 2))
        self.assertEqual(modfacts.version_tuple("24w14a"), ())
        self.assertEqual(modfacts.family("26.1"), "26.x")
        self.assertEqual(modfacts.family("1.20.1"), "1.20.1")
        self.assertEqual(modfacts.family("1.21.4"), "1.21.4")

    def test_java_and_metadata(self) -> None:
        self.assertEqual(modfacts.java_for("1.20.1"), 17)
        self.assertEqual(modfacts.java_for("1.21.1"), 21)
        self.assertEqual(modfacts.java_for("26.1"), 25)
        self.assertEqual(modfacts.metadata_for("neoforge", "1.21.1"),
                         "META-INF/neoforge.mods.toml")
        self.assertEqual(modfacts.metadata_for("neoforge", "1.20.4"),
                         "META-INF/mods.toml")
        self.assertEqual(modfacts.metadata_for("forge", "1.20.1"),
                         "META-INF/mods.toml")

    def test_data_dirs_and_item_definitions(self) -> None:
        self.assertEqual(modfacts.data_dirs("1.21.1")["recipe"], "recipe")
        self.assertEqual(modfacts.data_dirs("1.20.1")["recipe"], "recipes")
        self.assertTrue(modfacts.facts_for("fabric", "1.21.4").item_definitions)
        self.assertFalse(modfacts.facts_for("fabric", "1.21.1").item_definitions)

    def test_id_api(self) -> None:
        self.assertIn("Identifier.fromNamespaceAndPath",
                      modfacts.id_api_for("26.1"))
        self.assertIn("ResourceLocation.fromNamespaceAndPath",
                      modfacts.id_api_for("1.21.2"))
        self.assertIn("new ResourceLocation", modfacts.id_api_for("1.20.1"))


class ProjectCheckTest(unittest.TestCase):
    def _project(self, mc: str, loader: str, build: str, props: str = "") -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "build.gradle").write_text(build, encoding="utf-8")
        (root / "gradle.properties").write_text(props, encoding="utf-8")
        return root

    def test_java_mismatch(self) -> None:
        root = self._project(
            "1.21.1", "neoforge",
            "java.toolchain.languageVersion = JavaLanguageVersion.of(17)\n",
        )
        issues = modfacts.check_project(root, "neoforge", "1.21.1")
        self.assertTrue(any(item.rule == "java.mismatch" for item in issues))

    def test_data_plural_on_singular_version(self) -> None:
        root = self._project("1.21.1", "neoforge", "")
        data = root / "src" / "main" / "resources" / "data" / "demo" / "recipes"
        data.mkdir(parents=True)
        issues = modfacts.check_project(root, "neoforge", "1.21.1")
        self.assertTrue(any(item.rule == "data.plural" for item in issues))

    def test_26x_resource_location(self) -> None:
        root = self._project("26.1", "neoforge", "")
        src = root / "src" / "main" / "java" / "demo"
        src.mkdir(parents=True)
        (src / "Demo.java").write_text(
            "class Demo { Object id = new ResourceLocation(\"a\", \"b\"); }",
            encoding="utf-8",
        )
        issues = modfacts.check_project(root, "neoforge", "26.1")
        self.assertTrue(any(item.rule == "26x.identifier" for item in issues))

    def test_clean_project_has_no_errors(self) -> None:
        root = self._project(
            "1.20.1", "forge",
            "java.toolchain.languageVersion = JavaLanguageVersion.of(17)\n",
        )
        meta = root / "src" / "main" / "resources" / "META-INF"
        meta.mkdir(parents=True)
        (meta / "mods.toml").write_text("modId=\"demo\"\n", encoding="utf-8")
        issues = modfacts.check_project(root, "forge", "1.20.1")
        self.assertFalse([item for item in issues if item.severity == "error"])


class ArchRefTest(unittest.TestCase):
    def test_lookup_multiblock(self) -> None:
        hits = archref.lookup("多方块机器和配方映射", limit=2)
        self.assertTrue(hits)
        self.assertIn("gregtech", [item.id for item in hits])

    def test_catalog_mentions_all_profiles(self) -> None:
        text = archref.catalog()
        for key in ("create", "gregtech", "mekanism", "botania"):
            self.assertIn(key, text)

    def test_explain_license_and_patterns(self) -> None:
        text = archref.explain("mekanism")
        self.assertIn("API", text)
        self.assertIn("许可证", text)


class MultiVersionTest(unittest.TestCase):
    def test_choose_strategy(self) -> None:
        self.assertEqual(multiversion.choose(["fabric"], ["1.21.1"]), "single")
        self.assertEqual(multiversion.choose(["fabric"], ["1.21.1", "1.21.4"]),
                         "stonecutter")
        self.assertEqual(multiversion.choose(["fabric", "neoforge"], ["1.21.1"]),
                         "architectury")
        self.assertEqual(
            multiversion.choose(["fabric", "neoforge"], ["1.21.1", "1.21.4"]),
            "stonecraft",
        )

    def test_plan_contains_targets_and_warning(self) -> None:
        text = multiversion.plan(["fabric", "neoforge"], ["1.21.1", "1.21.4"])
        self.assertIn("1.21.1-fabric", text)
        self.assertIn("1.21.4-neoforge", text)
        self.assertIn("不要把“生成了目录”当成“多版本可用”", text)

    def test_single_plan(self) -> None:
        text = multiversion.plan(["fabric"], ["1.21.1"])
        self.assertIn("不需要多版本工程", text)


class CrashAndSkillsTest(unittest.TestCase):
    def test_java_class_version_pattern(self) -> None:
        report = crash.parse(
            "java.lang.UnsupportedClassVersionError: demo/Main has been compiled by "
            "a more recent version of the Java Runtime (class file version 69.0)"
        )
        kinds = [item["category"] for item in crash.classify(report)]
        self.assertIn("java-version", kinds)

    def test_access_widener_pattern(self) -> None:
        report = crash.parse("net.fabricmc.loader.impl.FormattedException: "
                             "AccessWidenerFormatException: invalid header")
        kinds = [item["category"] for item in crash.classify(report)]
        self.assertIn("access", kinds)

    def test_new_builtin_skills_present(self) -> None:
        ids = {item["id"] for item in skills.BUILTIN_SKILLS}
        for key in ("version-facts", "multiversion", "access-widener-transformer",
                    "gametest", "architecture-patterns", "publishing-ci"):
            self.assertIn(key, ids)

    def test_new_tools_registered(self) -> None:
        names = set(tool_schema.names())
        for key in ("version_facts", "project_fact_check", "arch_ref",
                    "multiversion_plan"):
            self.assertIn(key, names)


if __name__ == "__main__":
    unittest.main()