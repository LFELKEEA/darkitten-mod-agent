"""Config schema → screen + translation keys (P1-26, P3-16, P3-39).

The generated screen is deliberately loader-agnostic: it uses vanilla
``Screen``/``Button`` so it compiles on Forge, NeoForge and Fabric (Mojmap).
A mod can subclass it and bind its own config store through the callback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TYPES = {"bool", "int", "float", "string", "enum", "list"}


@dataclass
class ConfigField:
    key: str
    type: str = "bool"
    default: object = False
    min: float | None = None
    max: float | None = None
    values: list[str] = field(default_factory=list)
    comment: str = ""
    restart: str = "none"  # none | client | server | data
    feature: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "values": list(self.values),
            "comment": self.comment,
            "restart": self.restart,
            "feature": self.feature,
        }


def parse_schema(raw: object) -> list[ConfigField]:
    rows: list[ConfigField] = []
    if isinstance(raw, dict):
        items = raw.get("fields") if isinstance(raw.get("fields"), list) else []
        if not items:
            items = [
                {"key": key, **(value if isinstance(value, dict) else {"default": value})}
                for key, value in raw.items()
                if key not in {"fields", "schema", "version"}
            ]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("name") or "").strip()
        if not key:
            continue
        kind = str(item.get("type") or _infer_type(item.get("default"))).lower()
        kind = {"boolean": "bool", "integer": "int", "number": "float", "text": "string"}.get(
            kind, kind
        )
        if kind not in TYPES:
            kind = "string"
        rows.append(
            ConfigField(
                key=key,
                type=kind,
                default=item.get("default", _default_for(kind)),
                min=item.get("min"),
                max=item.get("max"),
                values=[str(value) for value in item.get("values") or []],
                comment=str(item.get("comment") or item.get("description") or ""),
                restart=str(item.get("restart") or "none"),
                feature=str(item.get("feature") or ""),
            )
        )
    return rows


def lang_entries(modid: str, fields: list[ConfigField], *, lang: str = "en_us") -> dict[str, str]:
    rows: dict[str, str] = {}
    for item in fields:
        key = f"config.{modid}.{item.key}"
        label = _humanize(item.key)
        rows[key] = label
        if item.comment:
            rows[f"{key}.tooltip"] = _translate_comment(item.comment, lang)
        if item.restart in {"client", "server"}:
            rows[f"{key}.restart"] = (
                "Requires a client restart" if lang == "en_us" else "需要重启客户端"
            ) if item.restart == "client" else (
                "Requires a server restart" if lang == "en_us" else "需要重启服务器"
            )
        if item.type == "enum" and item.values:
            for value in item.values:
                rows[f"{key}.{value}"] = _humanize(value)
    rows[f"config.{modid}.title"] = "Settings" if lang == "en_us" else "设置"
    return rows


def screen_source(
    *,
    package: str,
    modid: str,
    class_name: str = "ModConfigScreen",
    fields: list[ConfigField],
    mc: str = "1.20.1",
) -> str:
    package_line = f"package {package}.client;\n\n" if package else ""
    enum_maps = []
    toggle_cases = []
    for item in fields:
        if item.type == "enum" and item.values:
            values = ", ".join(f'"{value}"' for value in item.values)
            enum_maps.append(
                f'    private static final List<String> { _const(item.key) }_VALUES = List.of({values});'
            )
        if item.type == "int":
            step = "1"
        elif item.type == "float":
            step = "0.05"
        else:
            step = ""
        toggle_cases.append(
            f'        if ("{item.key}".equals(entry.getKey())) {{\n'
            f'            Object value = entry.getValue();\n'
            + _toggle_body(item, step)
            + "        }"
        )
    lines = [
        package_line.rstrip("\n"),
        "",
        "import java.util.ArrayList;",
        "import java.util.LinkedHashMap;",
        "import java.util.List;",
        "import java.util.Map;",
        "import java.util.function.Consumer;",
        "",
        "import net.minecraft.client.gui.GuiGraphics;",
        "import net.minecraft.client.gui.components.Button;",
        "import net.minecraft.client.gui.components.Tooltip;",
        "import net.minecraft.client.gui.screens.Screen;",
        "import net.minecraft.network.chat.Component;",
        "",
        "/**",
        f" * Generated by Darkitten Mod Agent for {modid}.",
        " * Bind `values` to your config store in `onSave`; every label is a",
        " * translation key so the screen works in any locale.",
        " */",
        f"public final class {class_name} extends Screen {{",
        "    private final Screen parent;",
        "    private final Map<String, Object> values;",
        "    private final Consumer<Map<String, Object>> onSave;",
        "",
    ]
    lines.extend(enum_maps)
    if enum_maps:
        lines.append("")
    lines.extend([
        f"    public {class_name}(Screen parent, Map<String, Object> values, Consumer<Map<String, Object>> onSave) {{",
        f'        super(Component.translatable("config.{modid}.title"));',
        "        this.parent = parent;",
        "        this.values = new LinkedHashMap<>(values);",
        "        this.onSave = onSave;",
        "    }",
        "",
        "    @Override",
        "    protected void init() {",
        "        int y = 36;",
        "        int x = Math.max(10, this.width / 2 - 150);",
        "        for (Map.Entry<String, Object> entry : new ArrayList<>(this.values.entrySet())) {",
        f'            String key = "config.{modid}." + entry.getKey();',
        "            Component label = Component.translatable(key)",
        '                    .append(Component.literal(": " + entry.getValue()));',
        "            Button button = Button.builder(label, b -> this.toggle(entry))",
        "                    .bounds(x, y, 300, 20)",
        "                    .build();",
        "            button.setTooltip(Tooltip.create(Component.translatable(key + \".tooltip\")));",
        "            this.addRenderableWidget(button);",
        "            y += 24;",
        "            if (y > this.height - 60) {",
        "                break;",
        "            }",
        "        }",
        '        this.addRenderableWidget(Button.builder(Component.translatable("gui.done"), b -> {',
        "            this.onSave.accept(this.values);",
        "            if (this.minecraft != null) {",
        "                this.minecraft.setScreen(this.parent);",
        "            }",
        "        }).bounds(this.width / 2 - 100, this.height - 32, 200, 20).build());",
        "    }",
        "",
        "    private void toggle(Map.Entry<String, Object> entry) {",
    ])
    lines.extend(toggle_cases)
    lines.extend([
        "        this.rebuildWidgets();",
        "    }",
        "",
        "    @Override",
        "    public void render(GuiGraphics graphics, int mouseX, int mouseY, float partialTick) {",
        ("        this.renderBackground(graphics, mouseX, mouseY, partialTick);" if _mc_at_least(mc, (1, 20, 2)) else "        this.renderBackground(graphics);"),
        "        graphics.drawCenteredString(this.font, this.title, this.width / 2, 16, 0xFFFFFF);",
        "        super.render(graphics, mouseX, mouseY, partialTick);",
        "    }",
        "",
        "    @Override",
        "    public void onClose() {",
        "        if (this.minecraft != null) {",
        "            this.minecraft.setScreen(this.parent);",
        "        }",
        "    }",
        "}",
        "",
    ])
    return "\n".join(lines)


def write(
    root: Path,
    *,
    package: str,
    modid: str,
    fields: list[ConfigField],
    class_name: str = "ModConfigScreen",
    mc: str = "1.20.1",
) -> dict:
    root = Path(root)
    package_path = package.replace(".", "/") if package else ""
    java = root / "src/main/java" / package_path / "client" / f"{class_name}.java"
    java.parent.mkdir(parents=True, exist_ok=True)
    java.write_text(
        screen_source(package=package, modid=modid, class_name=class_name, fields=fields, mc=mc),
        encoding="utf-8",
    )
    lang_root = root / "src/main/resources/assets" / modid / "lang"
    lang_root.mkdir(parents=True, exist_ok=True)
    written = [java]
    for lang, extra in (("en_us", "en_us"), ("zh_cn", "zh_cn")):
        target = lang_root / f"{lang}.json"
        existing: dict[str, str] = {}
        if target.is_file():
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = {str(key): str(value) for key, value in raw.items()}
            except ValueError:
                existing = {}
        rows = lang_entries(modid, fields, lang=lang)
        existing.update(rows)
        target.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        written.append(target)
    schema = root / "config" / f"{modid}.schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(
        json.dumps({"modid": modid, "fields": [item.to_dict() for item in fields]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written.append(schema)
    return {
        "files": [item.relative_to(root).as_posix() for item in written],
        "fields": len(fields),
        "translationKeys": len(lang_entries(modid, fields)),
    }


# ----------------------------------------------------------------------
def _toggle_body(item: ConfigField, step: str) -> str:
    if item.type == "bool":
        return "            entry.setValue(!Boolean.TRUE.equals(value));\n"
    if item.type == "int":
        low = int(item.min if item.min is not None else 0)
        high = int(item.max if item.max is not None else 1000)
        return (
            f"            int number = value instanceof Number n ? n.intValue() + 1 : {low};\n"
            f"            entry.setValue(number > {high} ? {low} : number);\n"
        )
    if item.type == "float":
        low = float(item.min if item.min is not None else 0.0)
        high = float(item.max if item.max is not None else 1.0)
        return (
            f"            float number = value instanceof Number n ? n.floatValue() + {step}f : {low}f;\n"
            f"            entry.setValue(number > {high}f ? {low}f : number);\n"
        )
    if item.type == "enum" and item.values:
        const = _const(item.key)
        return (
            f"            int index = value == null ? -1 : {const}_VALUES.indexOf(String.valueOf(value));\n"
            f"            entry.setValue({const}_VALUES.get((index + 1) % {const}_VALUES.size()));\n"
        )
    return "            // 字符串/列表字段请接入文本框或列表控件。\n"


def _mc_at_least(mc: str, target: tuple[int, ...]) -> bool:
    numbers = tuple(int(item) for item in re.findall(r"\d+", mc or "")[:3])
    return numbers >= target


def _const(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).upper()


def _infer_type(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _default_for(kind: str) -> object:
    return {
        "bool": False,
        "int": 0,
        "float": 0.0,
        "string": "",
        "enum": "",
        "list": [],
    }.get(kind, "")


def _humanize(value: str) -> str:
    text = re.sub(r"[._\-]+", " ", value).strip()
    return " ".join(part[:1].upper() + part[1:] for part in text.split()) or value


def _translate_comment(comment: str, lang: str) -> str:
    if lang == "zh_cn":
        return comment
    # Comments are usually Chinese in this product; keep them but strip the
    # risk of an empty tooltip.
    return comment or "Configure this option."
