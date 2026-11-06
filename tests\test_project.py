from __future__ import annotations

import json
import unittest
from pathlib import Path

from dma import project as project_mod
from dma.project import ProjectError

from .base import TempDataCase


class ProjectTests(TempDataCase):
    def make(self) -> project_mod.Project:
        return project_mod.create_project(
            self.tmp / "projects",
            name="星尘工具",
            mod_id="stardust_tools",
            mc_version="1.21.1",
            loader="Fabric",
            dependencies="Fabric API",
            task_type="创建全新模组",
            notes="不要用 Mixin",
        )

    def test_create_and_load(self) -> None:
        project = self.make()
        self.assertTrue((project.root / ".dma" / "project.json").is_file())
        self.assertTrue((project.root / "README.md").is_file())
        loaded = project_mod.load_project(project.root)
        self.assertEqual(loaded.meta.mod_id, "stardust_tools")
        self.assertEqual(loaded.meta.mc_version, "1.21.1")
        self.assertEqual(loaded.meta.loader, "Fabric")
        self.assertTrue(loaded.meta.complete())
        self.assertEqual(loaded.meta.missing(), [])

    def test_validate_mod_id(self) -> None:
        self.assertEqual(project_mod.validate_mod_id(" My_Mod1 "), "my_mod1")
        for value in ("", "1abc", "ab-c", "中文", "a"):
            with self.assertRaises(ProjectError):
                project_mod.validate_mod_id(value)

    def test_missing_fields(self) -> None:
        meta = project_mod.ProjectMeta()
        self.assertEqual(meta.missing(), ["MC 版本", "Mod ID"])

    def test_safe_path_rejects_escape(self) -> None:
        root = self.tmp / "root"
        root.mkdir()
        for value in ("../x.txt", "a/../../x.txt", "/etc/passwd", "C:/Windows/x.txt", ""):
            with self.assertRaises(ProjectError):
                project_mod.safe_path(root, value)
        target = project_mod.safe_path(root, "src/main/A.java")
        self.assertEqual(target, (root / "src/main/A.java").resolve())

    def test_write_read_search_and_backup(self) -> None:
        project = self.make()
        project_mod.write_text(project.root, "src/A.java", "class A {}\n")
        self.assertEqual(project_mod.read_text(project.root, "src/A.java"), "class A {}\n")
        project_mod.write_text(project.root, "src/A.java", "class A { int x; }\n")
        backups = list((self.tmp / "data" / "backups").rglob("A.java"))
        self.assertEqual(len(backups), 1)
        self.assertIn("class A {}", backups[0].read_text(encoding="utf-8"))
        hits = project_mod.search_text(project.root, "int x")
        self.assertTrue(hits and "src/A.java" in hits[0])
        tree = project_mod.file_tree(project.root)
        self.assertIn("src/A.java", tree)
        self.assertIn("README.md", tree)

    def test_write_strips_markdown_fences_from_code(self) -> None:
        project = self.make()
        content = "```java\npackage x;\n\npublic class A {}\n```\n"
        project_mod.write_text(project.root, "src/A.java", content)
        self.assertEqual(
            project_mod.read_text(project.root, "src/A.java"),
            "package x;\n\npublic class A {}\n",
        )
        # 只有结尾多出来的围栏也要清掉（实际工程里出现过的坏文件）
        broken = "package x;\n\npublic class B {}\n```\n"
        project_mod.write_text(project.root, "src/B.java", broken)
        self.assertEqual(
            project_mod.read_text(project.root, "src/B.java"),
            "package x;\n\npublic class B {}\n",
        )

    def test_write_keeps_fences_in_markdown(self) -> None:
        project = self.make()
        content = "```java\nclass A {}\n```\n"
        project_mod.write_text(project.root, "notes.md", content)
        self.assertEqual(project_mod.read_text(project.root, "notes.md"), content)

    def test_strip_wrapping_fences_only_touches_edges(self) -> None:
        body = "class A {\n    String s = \"```\";\n}\n"
        self.assertEqual(
            project_mod.strip_wrapping_fences("A.java", body), body
        )

    def test_repair_fences_scans_existing_sources(self) -> None:
        project = self.make()
        bad = project.root / "src" / "Bad.java"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("package x;\n\npublic class Bad {}\n```\n", encoding="utf-8")
        good = project.root / "src" / "Good.java"
        good.write_text("package x;\n\npublic class Good {}\n", encoding="utf-8")
        fixed = project_mod.repair_fences(project.root)
        self.assertEqual(fixed, ["src/Bad.java"])
        self.assertEqual(
            bad.read_text(encoding="utf-8"), "package x;\n\npublic class Bad {}\n"
        )
        self.assertEqual(project_mod.repair_fences(project.root), [])

    def test_repair_build_scripts_adds_neoforge_mods(self) -> None:
        root = self.tmp / "neoforge"
        root.mkdir()
        (root / "gradle.properties").write_text(
            "mod_id=stardust_tools\n", encoding="utf-8"
        )
        (root / "build.gradle").write_text(
            "neoForge {\n"
            "    version = project.neo_version\n"
            "    runs {\n"
            "        client { client() }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(project_mod.repair_build_scripts(root), ["build.gradle"])
        text = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn('mods {\n        "stardust_tools" {', text)
        self.assertIn("sourceSet sourceSets.main", text)
        self.assertLess(text.index("mods {"), text.index("runs {"))
        self.assertEqual(project_mod.repair_build_scripts(root), [])

    def test_repair_build_scripts_keeps_existing_mods(self) -> None:
        root = self.tmp / "neoforge"
        root.mkdir()
        original = (
            "neoForge {\n"
            '    mods {\n        "mine" {\n            sourceSet sourceSets.main\n'
            "        }\n    }\n"
            "    runs {\n        client { client() }\n    }\n"
            "}\n"
        )
        (root / "build.gradle").write_text(original, encoding="utf-8")
        self.assertEqual(project_mod.repair_build_scripts(root), [])
        self.assertEqual(
            (root / "build.gradle").read_text(encoding="utf-8"), original
        )

    def test_repair_build_scripts_fixes_data_run(self) -> None:
        root = self.tmp / "neoforge"
        root.mkdir()
        (root / "gradle.properties").write_text(
            "mod_id=stardust_tools\n", encoding="utf-8"
        )
        (root / "build.gradle").write_text(
            "neoForge {\n"
            "    runs {\n"
            "        data {\n"
            "            clientData()\n"
            "        }\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(project_mod.repair_build_scripts(root), ["build.gradle"])
        text = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertNotIn("clientData", text)
        self.assertIn("data()", text)
        self.assertIn("programArguments.addAll '--mod', 'stardust_tools'", text)
        self.assertIn("src/generated/resources", text)
        self.assertEqual(project_mod.repair_build_scripts(root), [])

    def test_repair_build_scripts_keeps_valid_data_run(self) -> None:
        root = self.tmp / "neoforge"
        root.mkdir()
        original = (
            "neoForge {\n"
            "    mods {\n"
            '        "mine" {\n'
            "            sourceSet sourceSets.main\n"
            "        }\n"
            "    }\n"
            "    runs {\n"
            "        data {\n"
            "            data()\n"
            "            programArguments.addAll '--mod', 'mine', '--all'\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        (root / "build.gradle").write_text(original, encoding="utf-8")
        self.assertEqual(project_mod.repair_build_scripts(root), [])
        self.assertEqual(
            (root / "build.gradle").read_text(encoding="utf-8"), original
        )

    def test_repair_build_scripts_adds_utf8_filtering(self) -> None:
        root = self.tmp / "neoforge"
        root.mkdir()
        (root / "build.gradle").write_text(
            "tasks.withType(ProcessResources).configureEach {\n"
            "    filesMatching(['META-INF/neoforge.mods.toml']) {\n"
            "        expand(version: project.version)\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        self.assertEqual(project_mod.repair_build_scripts(root), ["build.gradle"])
        text = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("filteringCharset = 'UTF-8'", text)
        self.assertEqual(project_mod.repair_build_scripts(root), [])

    def test_write_rejects_wrong_type(self) -> None:
        project = self.make()
        (project.root / "folder").mkdir()
        with self.assertRaises(ProjectError):
            project_mod.write_text(project.root, "folder", "x")

    def test_infer_from_fabric_metadata(self) -> None:
        root = self.tmp / "infer"
        resources = root / "src/main/resources"
        resources.mkdir(parents=True)
        (resources / "fabric.mod.json").write_text(
            json.dumps(
                {
                    "id": "hello_mod",
                    "name": "Hello Mod",
                    "depends": {"minecraft": "1.20.1"},
                }
            ),
            encoding="utf-8",
        )
        loaded = project_mod.load_project(root)
        self.assertEqual(loaded.meta.mod_id, "hello_mod")
        self.assertEqual(loaded.meta.name, "Hello Mod")
        self.assertEqual(loaded.meta.loader, "Fabric")
        self.assertEqual(loaded.meta.mc_version, "1.20.1")

    def test_recent_projects(self) -> None:
        project = self.make()
        rows = project_mod.recent_projects()
        self.assertEqual(rows[0]["root"], str(project.root))
        context = project_mod.project_context(project)
        self.assertIn("stardust_tools", context)
        self.assertIn("README.md", context)

    def test_delete_moves_project_to_trash(self) -> None:
        from dma import sessions

        project = self.make()
        sessions.save_history(project.root, [{"role": "user", "content": "你好"}])
        self.assertTrue(project_mod.recent_projects())
        destination = project_mod.delete_project(project.root, recycle=False)
        self.assertFalse(project.root.exists())
        self.assertIsNotNone(destination)
        self.assertTrue(Path(destination).is_dir())
        self.assertEqual(project_mod.recent_projects(), [])
        self.assertEqual(sessions.load_history(project.root), [])

    def test_delete_refuses_protected_directories(self) -> None:
        from dma import paths

        for target in (paths.data_dir(), Path.home()):
            with self.assertRaises(ProjectError):
                project_mod.delete_project(target, recycle=False)

    def test_delete_missing_project(self) -> None:
        missing = self.tmp / "not-there"
        with self.assertRaises(ProjectError):
            project_mod.delete_project(missing, recycle=False)

    def test_forget_project_keeps_other_entries(self) -> None:
        first = self.make()
        second = project_mod.create_project(
            self.tmp / "projects",
            name="第二个",
            mod_id="second_mod",
            mc_version="1.20.1",
            loader="Fabric",
        )
        project_mod.forget_project(first.root)
        roots = [item["root"] for item in project_mod.recent_projects()]
        self.assertNotIn(str(first.root), roots)
        self.assertIn(str(second.root), roots)


if __name__ == "__main__":
    unittest.main()
