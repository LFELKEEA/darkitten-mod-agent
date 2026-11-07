"""卡片 YAML 解析与场景步骤翻译。

只支持测试卡片用到的 YAML 子集（缩进映射/序列 + 行内 {}/[]），
与 yan-mc/minecraft-mod-dev（MIT）的 parseYaml / cardToSteps 行为一致。
"""

from __future__ import annotations

import json


def unquote(value: object) -> str:
    text = str(value).strip()
    if len(text) >= 2 and (
        (text[0] == '"' and text[-1] == '"')
        or (text[0] == "'" and text[-1] == "'")
    ):
        return text[1:-1]
    return text


def split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def split_key_value(text: str) -> tuple[str, str] | None:
    depth = 0
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == ":" and depth == 0:
            return text[:index], text[index + 1 :]
    return None


def parse_inline_value(text: str):
    raw = str(text).strip()
    if raw.startswith("{") and raw.endswith("}"):
        result: dict = {}
        inner = raw[1:-1].strip()
        if not inner:
            return result
        for part in split_top_level(inner, ","):
            pair = split_key_value(part)
            if pair is None:
                continue
            result[unquote(pair[0])] = parse_inline_value(pair[1])
        return result
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [parse_inline_value(item) for item in split_top_level(inner, ",")]
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw in {"null", "~"}:
        return None
    if _is_int(raw):
        return int(raw)
    if _is_float(raw):
        return float(raw)
    return raw


def _is_int(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    return bool(body) and body.isdigit()


def _is_float(text: str) -> bool:
    body = text[1:] if text.startswith("-") else text
    if body.count(".") != 1:
        return False
    left, right = body.split(".", 1)
    return left.isdigit() and right.isdigit()


def parse_yaml(text: str):
    lines: list[dict] = []
    for raw in str(text).splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        stripped = raw.lstrip(" ")
        indent = len(raw) - len(stripped)
        lines.append({"indent": indent, "content": stripped.strip()})
    if not lines:
        return {}
    state = {"index": 0}

    def parse_block(indent: int):
        if state["index"] >= len(lines):
            return None
        line = lines[state["index"]]
        if line["indent"] < indent:
            return None
        if line["content"].startswith("-"):
            return parse_sequence(line["indent"])
        return parse_mapping(line["indent"])

    def parse_sequence(indent: int) -> list:
        result: list = []
        while state["index"] < len(lines):
            line = lines[state["index"]]
            if line["indent"] != indent or not line["content"].startswith("-"):
                break
            rest = line["content"][1:].strip()
            state["index"] += 1
            if rest == "":
                nxt = lines[state["index"]] if state["index"] < len(lines) else None
                result.append(parse_block(nxt["indent"]) if nxt and nxt["indent"] > indent else None)
            elif rest.startswith("{") or rest.startswith("["):
                result.append(parse_inline_value(rest))
            elif split_key_value(rest) is not None:
                pair = split_key_value(rest)
                assert pair is not None
                item: dict = {unquote(pair[0]): parse_inline_value(pair[1])}
                # 支持 `- key:` 后面跟缩进子块的写法。
                if str(pair[1]).strip() == "":
                    nxt = lines[state["index"]] if state["index"] < len(lines) else None
                    if nxt and nxt["indent"] > indent:
                        item[unquote(pair[0])] = parse_block(nxt["indent"])
                result.append(item)
            else:
                result.append(parse_inline_value(rest))
        return result

    def parse_mapping(indent: int) -> dict:
        result: dict = {}
        while state["index"] < len(lines):
            line = lines[state["index"]]
            if line["indent"] != indent or line["content"].startswith("-"):
                break
            pair = split_key_value(line["content"])
            if pair is None:
                break
            key = unquote(pair[0])
            rest = pair[1].strip()
            state["index"] += 1
            if rest == "":
                nxt = lines[state["index"]] if state["index"] < len(lines) else None
                result[key] = parse_block(nxt["indent"]) if nxt and nxt["indent"] > indent else None
            else:
                result[key] = parse_inline_value(rest)
        return result

    return parse_block(lines[0]["indent"])


def map_card_op(op: object) -> str:
    value = str(op if op is not None else "eq").strip()
    mapping = {
        "==": "eq",
        "=": "eq",
        "eq": "eq",
        "!=": "ne",
        "ne": "ne",
        ">": "gt",
        "gt": "gt",
        ">=": "gte",
        "gte": "gte",
        "<": "lt",
        "lt": "lt",
        "<=": "lte",
        "lte": "lte",
        "contains": "contains",
        "exists": "exists",
    }
    return mapping.get(value, value)


def card_to_steps(card: dict) -> dict:
    steps: list[dict] = []
    warnings: list[str] = []

    setup = card.get("setup") if isinstance(card.get("setup"), dict) else {}
    commands = setup.get("commands")
    if isinstance(commands, list):
        for cmd in commands:
            if isinstance(cmd, str) and cmd.strip():
                steps.append({"type": "command", "side": "server", "cmd": cmd})

    actions = card.get("actions") if isinstance(card.get("actions"), list) else []
    for action in actions:
        if isinstance(action, str):
            steps.append({"type": "command", "side": "server", "cmd": action})
            continue
        if not isinstance(action, dict):
            continue
        if action.get("give"):
            give = action["give"] if isinstance(action["give"], dict) else {}
            player = give.get("player") or "Dev"
            item = give.get("item")
            count = give.get("count") or 1
            if isinstance(item, str):
                steps.append(
                    {
                        "type": "command",
                        "side": "server",
                        "cmd": f"give {player} {item} {count}",
                    }
                )
                if give.get("select"):
                    tagged = (
                        f"{item}{{{give.get('nbt')}}}" if give.get("nbt") else item
                    )
                    steps.append(
                        {
                            "type": "command",
                            "side": "server",
                            "cmd": f"item replace entity {player} weapon.mainhand with {tagged} {count}",
                        }
                    )
        elif action.get("select_item"):
            select = action["select_item"] if isinstance(action["select_item"], dict) else {}
            player = select.get("player") or "Dev"
            count = select.get("count") or 1
            if isinstance(select.get("item"), str):
                tagged = (
                    f"{select['item']}{{{select.get('nbt')}}}"
                    if select.get("nbt")
                    else select["item"]
                )
                steps.append(
                    {
                        "type": "command",
                        "side": "server",
                        "cmd": f"item replace entity {player} weapon.mainhand with {tagged} {count}",
                    }
                )
        elif action.get("use_on") or action.get("use_item_on"):
            use = action.get("use_on") or action.get("use_item_on")
            if isinstance(use, dict):
                steps.append(
                    {
                        "type": "use",
                        "x": use.get("x"),
                        "y": use.get("y"),
                        "z": use.get("z"),
                        "face": use.get("face") or "up",
                    }
                )
        elif action.get("craft"):
            craft = action["craft"] if isinstance(action["craft"], dict) else {}
            if isinstance(craft.get("recipe"), str):
                steps.append(
                    {
                        "type": "craft",
                        "id": craft["recipe"],
                        "player": craft.get("player") or "Dev",
                    }
                )
                steps.append(
                    {"type": "assert", "path": "data.crafted", "op": "eq", "value": "true"}
                )
            else:
                warnings.append("craft requires recipe")
        elif action.get("close_ui"):
            steps.append({"type": "ui_close"})
        elif action.get("mine"):
            mine = action["mine"] if isinstance(action["mine"], dict) else {}
            steps.append(
                {
                    "type": "mine",
                    "x": mine.get("x", 0),
                    "y": mine.get("y", 64),
                    "z": mine.get("z", 0),
                    "player": mine.get("player") or "Dev",
                }
            )
        elif action.get("client_mine"):
            mine = action["client_mine"] if isinstance(action["client_mine"], dict) else {}
            steps.append(
                {
                    "type": "client_mine",
                    "x": mine.get("x", 0),
                    "y": mine.get("y", 64),
                    "z": mine.get("z", 0),
                    "timeoutMs": mine.get("timeoutMs", 6000),
                }
            )
        elif isinstance(action.get("wait"), (int, float)):
            steps.append({"type": "wait", "ms": action["wait"]})
        elif isinstance(action.get("command"), str):
            steps.append({"type": "command", "side": "server", "cmd": action["command"]})
        else:
            warnings.append(f"unsupported action: {action}")

    asserts = card.get("assert") if isinstance(card.get("assert"), list) else []
    for check in asserts:
        if not isinstance(check, dict):
            continue
        if "screen_title" in check:
            steps.append({"type": "ui"})
            steps.append(
                {
                    "type": "assert",
                    "path": "data.title",
                    "op": "eq",
                    "value": str(check["screen_title"]),
                }
            )
        elif "slot_exists" in check:
            slot = int(check["slot_exists"])
            steps.append({"type": "ui"})
            steps.append(
                {
                    "type": "assert",
                    "path": f"data.slots.{slot}.index",
                    "op": "eq",
                    "value": str(slot),
                }
            )
        elif check.get("block_entity_nbt"):
            nbt = check["block_entity_nbt"] if isinstance(check["block_entity_nbt"], dict) else {}
            pos = nbt.get("pos")
            if isinstance(pos, list) and len(pos) == 3:
                x, y, z = pos
                path = nbt.get("path") or "Energy"
                steps.append(
                    {
                        "type": "command",
                        "side": "server",
                        "cmd": f"data get block {x} {y} {z} {path}",
                    }
                )
                steps.append(
                    {
                        "type": "assert",
                        "path": "data.result",
                        "op": map_card_op(nbt.get("op")),
                        "value": str(nbt.get("value")),
                    }
                )
            else:
                warnings.append("block_entity_nbt requires pos=[x,y,z]")
        elif check.get("comparator"):
            comp = check["comparator"] if isinstance(check["comparator"], dict) else {}
            pos = comp.get("pos")
            if isinstance(pos, list) and len(pos) == 3:
                x, y, z = pos
                steps.append({"type": "block", "x": x, "y": y, "z": z})
                steps.append(
                    {
                        "type": "assert",
                        "path": "data.analog",
                        "op": map_card_op(comp.get("op")),
                        "value": str(comp.get("value")),
                    }
                )
            else:
                warnings.append("comparator requires pos=[x,y,z]")
        elif check.get("recipe_output"):
            recipe = check["recipe_output"] if isinstance(check["recipe_output"], dict) else {}
            rid = recipe.get("recipe") or recipe.get("id")
            if isinstance(rid, str):
                steps.append({"type": "recipe", "id": rid})
                if "item" in recipe:
                    steps.append(
                        {
                            "type": "assert",
                            "path": "data.item",
                            "op": "eq",
                            "value": str(recipe.get("item")),
                        }
                    )
                if "count" in recipe:
                    steps.append(
                        {
                            "type": "assert",
                            "path": "data.count",
                            "op": "eq",
                            "value": str(recipe.get("count")),
                        }
                    )
            else:
                warnings.append("recipe_output requires recipe id")
        elif check.get("loot_drop"):
            loot = check["loot_drop"] if isinstance(check["loot_drop"], dict) else {}
            if isinstance(loot.get("block"), str):
                steps.append({"type": "loot", "block": loot["block"]})
                _append_loot_asserts(steps, loot)
            else:
                warnings.append("loot_drop requires block")
        elif check.get("loot_break"):
            loot = check["loot_break"] if isinstance(check["loot_break"], dict) else {}
            x = loot.get("x", 0)
            y = loot.get("y", 64)
            z = loot.get("z", 0)
            if isinstance(loot.get("block"), str):
                steps.append(
                    {"type": "command", "side": "server", "cmd": f"setblock {x} {y} {z} {loot['block']}"}
                )
            steps.append({"type": "break", "x": x, "y": y, "z": z})
            _append_loot_asserts(steps, loot)
        elif check.get("block_state"):
            state = check["block_state"] if isinstance(check["block_state"], dict) else {}
            steps.append(
                {
                    "type": "block",
                    "x": state.get("x", 0),
                    "y": state.get("y", 64),
                    "z": state.get("z", 0),
                }
            )
            steps.append(
                {
                    "type": "assert",
                    "path": "data.block",
                    "op": "eq",
                    "value": str(state.get("block")),
                }
            )
        elif check.get("loot_items"):
            loot = check["loot_items"] if isinstance(check["loot_items"], dict) else {}
            steps.append(
                {
                    "type": "items",
                    "x": loot.get("x", 0),
                    "y": loot.get("y", 64),
                    "z": loot.get("z", 0),
                    "r": loot.get("r", 4),
                }
            )
            _append_loot_asserts(steps, loot)
        elif check.get("loot_mine"):
            loot = check["loot_mine"] if isinstance(check["loot_mine"], dict) else {}
            x = loot.get("x", 0)
            y = loot.get("y", 64)
            z = loot.get("z", 0)
            if isinstance(loot.get("block"), str):
                steps.append(
                    {"type": "command", "side": "server", "cmd": f"setblock {x} {y} {z} {loot['block']}"}
                )
            steps.append(
                {"type": "mine", "x": x, "y": y, "z": z, "player": loot.get("player") or "Dev"}
            )
            _append_loot_asserts(steps, loot)
        elif check.get("item_bar"):
            bar = check["item_bar"] if isinstance(check["item_bar"], dict) else {}
            if isinstance(bar.get("item"), str):
                steps.append({"type": "item", "item": bar["item"], "nbt": bar.get("nbt")})
                if "visible" in bar:
                    steps.append(
                        {
                            "type": "assert",
                            "path": "data.visible",
                            "op": "eq",
                            "value": str(bar.get("visible")),
                        }
                    )
                if "width" in bar:
                    steps.append(
                        {
                            "type": "assert",
                            "path": "data.width",
                            "op": "eq",
                            "value": str(bar.get("width")),
                        }
                    )
                if "color" in bar:
                    steps.append(
                        {
                            "type": "assert",
                            "path": "data.color",
                            "op": "eq",
                            "value": str(bar.get("color")),
                        }
                    )
            else:
                warnings.append("item_bar requires item")
        elif "no_exception" in check:
            # 探针本身报错就会让执行失败，这里不需要额外步骤。
            continue
        else:
            key = next(iter(check), "unknown")
            warnings.append(f"unsupported assert: {key}")
    return {"steps": steps, "warnings": warnings}


def _append_loot_asserts(steps: list[dict], loot: dict) -> None:
    if "item" in loot:
        steps.append(
            {
                "type": "assert",
                "path": "data.drops.0.item",
                "op": "eq",
                "value": str(loot.get("item")),
            }
        )
    if "count" in loot:
        steps.append(
            {
                "type": "assert",
                "path": "data.total",
                "op": "eq",
                "value": str(loot.get("count")),
            }
        )
