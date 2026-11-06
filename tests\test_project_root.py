from __future__ import annotations

import json
import unittest
from pathlib import Path

from dma import paths
from dma.config import Config, load_config, save_config

from .base import TempDataCase


class ProjectRootTests(TempDataCase):
    def test_default_project_root_inside_data_dir(self) -> None:
        root = paths.default_project_root()
        self.assertEqual(root, self.tmp / "data" / "projects")
        self.assertTrue(root.is_dir())

    def test_empty_config_uses_new_default(self) -> None:
        config = Config().normalized()
        self.assertEqual(Path(config.project_root), self.tmp / "data" / "projects")

    def test_legacy_default_is_migrated(self) -> None:
        target = self.tmp / "data" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy = str(Path.home() / "Documents" / "DarkittenModProjects")
        target.write_text(
            json.dumps({"project_root": legacy}, ensure_ascii=False), encoding="utf-8"
        )
        config = load_config(target)
        self.assertEqual(Path(config.project_root), self.tmp / "data" / "projects")

    def test_custom_project_root_is_kept(self) -> None:
        custom = self.tmp / "my-projects"
        target = self.tmp / "data" / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        save_config(Config(project_root=str(custom)), target)
        config = load_config(target)
        self.assertEqual(Path(config.project_root), custom)
