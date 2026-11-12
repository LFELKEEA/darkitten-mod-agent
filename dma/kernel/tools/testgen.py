"""Test generation: JUnit, GameTest and scenario cards (P1-01 / P1-02 / P1-05)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..spec import Feature, ModSpec


@dataclass
class Suite:
    feature_id: str
    title: str
    tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"feature": self.feature_id, "title": self.title, "tests": list(self.tests)}


def plan(spec: ModSpec, *, features: Iterable[str] | None = None) -> list[Suite]:
    wanted = set(features or [])
    rows: list[Suite] = []
    for feature in spec.features:
        if wanted and feature.id not in wanted:
            continue
        if feature.id in {"acceptance"}:
            continue
        tests = _tests_for(feature)
        rows.append(Suite(feature.id, feature.title, tests))
    return rows


def unit_test_source(spec: ModSpec, feature: Feature, *, package: str | None = None) -> str:
    package = package or spec.package
    class_name = _class_name(feature.id)
    lines = [
        f"package {package};",
        "",
        "import static org.junit.jupiter.api.Assertions.assertNotNull;",
        "import static org.junit.jupiter.api.Assertions.assertTrue;",
        "",
        "import org.junit.jupiter.api.Test;",
        "",
        f"class {class_name}Test {{",
        "",
        "    @Test",
        "    void featureIsWired() {",
        f"        // {feature.title}: {feature.description[:80]}",
        "        assertTrue(true, \"占位断言：替换为真实行为断言\");",
        "    }",
    ]
    for index, test in enumerate(_tests_for(feature)[:6], 1):
        lines.extend([
            "",
            "    @Test",
            f"    void case{index:02d}_{_method_name(test)}() throws Exception {{",
            "        assertNotNull(this, \"待实现：构造被测对象并断言结果\");",
            "    }",
        ])
    lines.append("}")
    return "\n".join(lines) + "\n"


def _game_test_loader(spec: ModSpec) -> str:
    lowered = (spec.loader or "").strip().lower().replace("neo forge", "neoforge")
    if "fabric" in lowered:
        return "fabric"
    if "neoforge" in lowered:
        return "neoforge"
    if "forge" in lowered:
        return "forge"
    return "neoforge"


def _game_test_imports(loader: str) -> list[str]:
    if loader == "fabric":
        return ["import net.fabricmc.fabric.api.gametest.v1.FabricGameTest;"]
    if loader == "forge":
        return [
            "import net.minecraftforge.event.RegisterGameTestsEvent;",
            "import net.minecraftforge.eventbus.api.SubscribeEvent;",
        ]
    return [
        "import net.neoforged.neoforge.event.RegisterGameTestsEvent;",
        "import net.neoforged.bus.api.SubscribeEvent;",
    ]


def game_test_source(spec: ModSpec, feature: Feature, *, package: str | None = None) -> str:
    package = package or spec.package
    class_name = _class_name(feature.id)
    loader = _game_test_loader(spec)
    lines = [
        f"package {package};",
        "",
        "import net.minecraft.gametest.framework.GameTest;",
        "import net.minecraft.gametest.framework.GameTestHelper;",
        *_game_test_imports(loader),
        "",
        "public class " + class_name + "GameTest" + (" implements FabricGameTest" if loader == "fabric" else "") + " {",
        "",
        "    @GameTest(template = \"minecraft:empty\")",
        "    public void scenario(GameTestHelper helper) {",
        "        // 前置清理：确保方块/实体不残留",
        f"        // {feature.title}: 失败时用 helper.fail(...) 输出具体坐标",
        "        helper.succeed();",
        "    }",
    ]
    if loader != "fabric":
        lines += [
            "",
            "    @SubscribeEvent",
            "    public static void register(RegisterGameTestsEvent event) {",
            f"        event.register({class_name}GameTest.class);",
            "    }",
        ]
    lines.append("}")
    return "\n".join(lines) + "\n"


def scenario_card(spec: ModSpec, feature: Feature) -> str:
    lines = [
        f"# 场景卡：{feature.title}",
        "",
        f"- 功能 ID：`{feature.id}`",
        f"- 目标平台：{spec.loader} {spec.mc}",
        f"- 描述：{feature.description}",
        "",
        "## 前置条件",
        "",
        "- 新建超平坦世界，关闭天气与随机 tick",
        "- 清理同名方块/实体/掉落物",
        "",
        "## 步骤",
        "",
    ]
    for index, test in enumerate(_tests_for(feature), 1):
        lines.append(f"{index}. {test}")
    lines.extend([
        "",
        "## 通过标准",
        "",
        "- 没有崩溃/异常日志",
        "- 服务端权威：客户端不能伪造结果",
        "- 存档退出再进入，状态保持一致",
        "",
        "## 证据",
        "",
        "- GameTest 输出片段",
        "- 截图（打开 GUI / 放置方块 / 实体行为）",
        "- 最新日志中无 ERROR",
    ])
    return "\n".join(lines) + "\n"


def write_suite(
    root: Path,
    spec: ModSpec,
    *,
    package: str | None = None,
    features: Iterable[str] | None = None,
    game_test: bool = True,
) -> list[Path]:
    root = Path(root)
    package = package or spec.package
    folder = Path(*package.split("."))
    written: list[Path] = []
    for suite in plan(spec, features=features):
        feature = next((item for item in spec.features if item.id == suite.feature_id), None)
        if feature is None:
            continue
        unit_path = root / "src/test/java" / folder / f"{_class_name(feature.id)}Test.java"
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_test_source(spec, feature, package=package), encoding="utf-8")
        written.append(unit_path)
        if game_test:
            game_path = root / "src/gametest/java" / folder / f"{_class_name(feature.id)}GameTest.java"
            game_path.parent.mkdir(parents=True, exist_ok=True)
            game_path.write_text(game_test_source(spec, feature, package=package), encoding="utf-8")
            written.append(game_path)
        scenario_path = root / "scenarios" / f"{feature.id}.md"
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_path.write_text(scenario_card(spec, feature), encoding="utf-8")
        written.append(scenario_path)
    return written


def matrix(spec: ModSpec) -> dict:
    return {
        "features": [suite.to_dict() for suite in plan(spec)],
        "versions": [spec.mc, *[item for item in (spec.constraints or []) if "target=" in item]],
    }


# ----------------------------------------------------------------------
def _tests_for(feature: Feature) -> list[str]:
    base = [f"{feature.title}：正常路径"]
    category = feature.id
    extras = {
        "item": ["堆叠/容器边界", "服务端拒绝非法使用"],
        "block": ["破坏掉落与正确工具", "方块状态组合"],
        "entity": ["生成/卸载/死亡数据", "AI 目标优先级"],
        "network": ["非法包被拒绝", "断线/切维度清理"],
        "gui": ["Shift 转移与关闭清理", "高 DPI/超长提示"],
        "data": ["标签 merge/replace", "配方/战利品 schema"],
        "assets": ["模型/贴图缺失降级", "动画名称一致"],
        "dimension": ["传送与重生点", "高度边界/虚空"],
        "command": ["权限与参数解析", "执行源区分"],
        "config": ["类型/范围/默认值", "旧配置迁移"],
    }
    return base + extras.get(category, ["边界输入", "失败路径与日志"])


def _class_name(feature_id: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", feature_id)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "Feature"


def _method_name(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", text)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "case"
    if re.match(r"^\d", cleaned):
        cleaned = "case_" + cleaned
    return cleaned[:48]


def write_matrix(root: Path, spec: ModSpec) -> Path:
    target = Path(root) / "scenarios" / "matrix.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(matrix(spec), ensure_ascii=False, indent=2), encoding="utf-8")
    return target
