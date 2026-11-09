"""Data-driven NPC, dialogue, quest and reputation framework.

The social layer deliberately avoids custom networking, custom screens and
SavedData serialisation. Everything runs on three vanilla systems that have
been stable for a decade:

* **scoreboard objectives** for relationship / reputation values
* **player tags** for quest state and one-shot story flags
* **data-pack functions** for dialogue, rewards and quest-board output

That means an NPC, a dialogue tree and a full quest chain work on Forge,
NeoForge and Fabric with the same data, survive world restarts, and stay
inspectable with vanilla commands. The generated Java only needs to spawn the
NPC entity and call one function when the player right-clicks it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assets import data_dir, tag_dir
from .util import write_json, write_text


def _path(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def _key(mc: str) -> tuple[int, int, int]:
    if mc.startswith("26."):
        return (26, 0, 0)
    parts = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    return parts + (0,) * (3 - len(parts))


def _short(value: str, limit: int = 16) -> str:
    value = re.sub(r"[^a-z0-9_]", "_", value.lower())
    return value[:limit] or "x"


def _objective(npc: str) -> str:
    return _short("dma_r_" + npc, 16)


def _tag(prefix: str, value: str) -> str:
    return _short(prefix + "_" + value, 48)


def _uses_typed_predicates(mc: str) -> bool:
    """26.x switched loot predicates from ``condition``+list to typed objects."""
    return _key(mc) >= (26, 0, 0)


def _player_condition(mc: str, tag: str) -> dict[str, Any]:
    if _uses_typed_predicates(mc):
        return {
            "type": "minecraft:entity_properties",
            "entity": "this",
            "predicate": {"minecraft:nbt": "{Tags:[\"" + tag + "\"]}"},
        }
    return {
        "condition": "minecraft:entity_properties",
        "entity": "this",
        "predicate": {"nbt": "{Tags:[\"" + tag + "\"]}"},
    }


def _player_conditions(mc: str, tag: str) -> dict[str, Any]:
    condition = _player_condition(mc, tag)
    if _uses_typed_predicates(mc):
        return {"player": condition}
    return {"player": [condition]}


def _json_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _click_component(text: str, command: str, mc: str, color: str = "yellow") -> dict[str, Any]:
    if _key(mc) >= (1, 21, 5):
        return {
            "text": text,
            "color": color,
            "click_event": {"action": "run_command", "command": command},
        }
    return {
        "text": text,
        "color": color,
        "clickEvent": {"action": "run_command", "value": command},
    }


@dataclass
class Quest:
    id: str
    title: str
    description: str
    stage: int
    objectives: list[dict[str, Any]] = field(default_factory=list)
    rewards: dict[str, Any] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)
    unlocks: list[str] = field(default_factory=list)
    giver: str = ""

    @property
    def tag(self) -> str:
        return _tag("dma_quest", self.id)

    @property
    def unlocked_tag(self) -> str:
        return _tag("dma_unlocked", self.id)

    @property
    def done_tag(self) -> str:
        return _tag("dma_done", self.id)


@dataclass
class Npc:
    id: str
    name_zh: str
    name_en: str
    description: str
    dialogue: str = ""
    marriageable: bool = False
    schedule: list[dict[str, Any]] = field(default_factory=list)
    quests: list[str] = field(default_factory=list)
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)


@dataclass
class SocialPlan:
    npcs: list[Npc]
    quests: list[Quest]
    dialogues: dict[str, dict[str, Any]]
    settings: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "npcs": [n.__dict__ for n in self.npcs],
            "quests": [q.__dict__ for q in self.quests],
            "dialogues": self.dialogues,
            "settings": self.settings,
        }


def build_plan(spec: dict[str, Any]) -> SocialPlan:
    modid = spec["identity"]["modid"]
    nodes = spec["content"]
    npc_nodes = [n for n in nodes if n["type"] == "npc"]
    quest_nodes = [n for n in nodes if n["type"] == "quest"]
    text_nodes = [n for n in nodes if n["type"] == "dialogue"]

    npcs: list[Npc] = []
    for node in npc_nodes:
        nid = _path(node["id"])
        npcs.append(Npc(
            id=nid,
            name_zh=node["name_zh"],
            name_en=node["name_en"],
            description=node.get("description", ""),
            dialogue=f"{modid}:{nid}/root",
            marriageable="marriage" in " ".join(node.get("mechanics", [])).lower() or nid in {"guide", "baker"},
            schedule=[
                {"time": 0, "activity": "sleep"},
                {"time": 6000, "activity": "work"},
                {"time": 12000, "activity": "social"},
                {"time": 18000, "activity": "home"},
            ],
            quests=[],
            likes=node.get("tags", []),
        ))
    if not npcs:
        return SocialPlan(npcs=[], quests=[], dialogues={}, settings={})

    # A quest's giver is the first NPC it depends on, otherwise the first NPC.
    npc_ids = {n.id for n in npcs}
    for npc in npcs:
        npc.quests = []
    quests: list[Quest] = []
    for index, node in enumerate(quest_nodes):
        nid = _path(node["id"])
        giver = next((d.split(":", 1)[-1] for d in node.get("dependencies", [])
                      if d.split(":", 1)[-1] in npc_ids), npcs[0].id)
        mechanics = " ".join(node.get("mechanics", [])).lower()
        objectives: list[dict[str, Any]] = []
        if "objectives" in mechanics or "talk" in mechanics or not mechanics:
            objectives.append({"type": "talk", "target": giver, "count": 1})
            objectives.append({"type": "item", "item": f"{modid}:{_path(nodes[0]['id'])}", "count": 4})
        if "kill" in mechanics:
            objectives.append({"type": "kill", "entity": f"{modid}:knight", "count": 1})
        if "relationship" in mechanics:
            objectives.append({"type": "relationship", "target": giver, "count": 10})
        if not objectives:
            objectives.append({"type": "talk", "target": giver, "count": 1})
        material = next((n["id"] for n in nodes
                         if n["type"] in {"material", "item", "food", "artifact"}), nodes[0]["id"])
        quest = Quest(
            id=nid,
            title=node["name_zh"],
            description=node.get("description", ""),
            stage=node.get("stage", 2),
            objectives=objectives,
            rewards={"xp": 50 + index * 25, "relationship": {giver: 5},
                     "items": [material]},
            requires=[],
            giver=giver,
        )
        quests.append(quest)
        npc = next((n for n in npcs if n.id == giver), npcs[0])
        npc.quests.append(nid)
        if "marriage" in mechanics:
            npc.marriageable = True
    # Link the chain in stage order: each quest unlocks the next one.
    quests.sort(key=lambda q: (q.stage, q.id))
    for current, following in zip(quests, quests[1:]):
        current.unlocks.append(following.id)
        following.requires.append(current.id)

    dialogues: dict[str, dict[str, Any]] = {}
    for npc in npcs:
        root = f"{npc.id}/root"
        options = [
            {"label": "这里是什么地方？", "function": f"{npc.id}/about"},
            {"label": "有什么可以帮忙的？", "function": f"{npc.id}/quests"},
            {"label": "再见。", "action": "close"},
        ]
        dialogues[root] = {
            "speaker": npc.id,
            "lines": [f"你好，旅行者。我是{npc.name_zh}。"],
            "options": options,
        }
        dialogues[f"{npc.id}/about"] = {
            "speaker": npc.id,
            "lines": [npc.description or f"{npc.name_zh}正在忙自己的事。"],
            "options": [{"label": "回到话题。", "function": f"{npc.id}/root"}],
        }
        dialogues[f"{npc.id}/quests"] = {
            "speaker": npc.id,
            "lines": ["让我看看最近有什么需要帮忙的。"],
            "options": [{"label": "回到话题。", "function": f"{npc.id}/root"}],
            "quests": npc.quests,
        }
        if npc.marriageable and any("marriage" in q.id for q in quests):
            dialogues[f"{npc.id}/marriage"] = {
                "speaker": npc.id,
                "lines": ["我们经历了这么多……你愿意留下来吗？"],
                "options": [
                    {"label": "我愿意。", "function": f"{npc.id}/marry"},
                    {"label": "再想想。", "function": f"{npc.id}/root"},
                ],
                "requires": {"relationship": 30},
            }
    marriage = next((q for q in quests if "marriage" in q.id), None)
    if marriage and "marriage" not in dialogues.get(f"{npcs[0].id}/marriage", {}):
        dialogues[f"{npcs[0].id}/marriage"] = {
            "speaker": npcs[0].id,
            "lines": ["我们经历了这么多……你愿意留下来吗？"],
            "options": [{"label": "我愿意。", "function": f"{npcs[0].id}/marry"}],
            "requires": {"relationship": 30},
        }

    settings = {
        "schema": "mcmod-social/1",
        "modid": modid,
        "relationship_objectives": {n.id: _objective(n.id) for n in npcs},
        "thresholds": {"friend": 10, "close": 20, "marriage": 30},
        "reputation_objective": "dma_reputation",
        "marriage_quest": marriage.id if marriage else "",
        "dialogue_root": {n.id: f"{modid}:{n.id}/root" for n in npcs},
        "notes": [
            "关系值存储在记分板，任务状态存储在玩家标签，世界重启后仍然有效。",
            "对话通过可点击聊天执行数据包函数，不需要自定义网络包或 GUI。",
        ],
    }
    return SocialPlan(npcs=npcs, quests=quests, dialogues=dialogues, settings=settings)


def _icon_key(mc: str) -> str:
    return "id" if _key(mc) >= (1, 20, 5) else "item"


def _tellraw(lines: list[dict[str, Any]]) -> str:
    payload = [{"text": ""}] + lines
    return "tellraw @s " + _json_line(payload)


def _speaker_line(name: str, text: str) -> dict[str, Any]:
    return [
        {"text": f"[{name}] ", "color": "gold"},
        {"text": text, "color": "white"},
    ]


def _write_function(res: Path, mc: str, modid: str, rel: str, commands: list[str]) -> None:
    path = res / "data" / modid / "function" / (rel + ".mcfunction")
    write_text(path, "\n".join(commands) + "\n")


def _quest_advancement(quest: Quest, modid: str, mc: str, npc: Npc | None) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    item_obj = next((o for o in quest.objectives if o["type"] == "item"), None)
    kill_obj = next((o for o in quest.objectives if o["type"] == "kill"), None)
    talk_obj = next((o for o in quest.objectives if o["type"] == "talk"), None)
    rel_obj = next((o for o in quest.objectives if o["type"] == "relationship"), None)
    if item_obj:
        criteria["items"] = {
            "trigger": "minecraft:inventory_changed",
            "conditions": {
                "items": [{
                    "items": item_obj["item"],
                    "count": {"min": int(item_obj.get("count", 1))},
                }],
            },
        }
    if kill_obj:
        if _uses_typed_predicates(mc):
            criteria["kill"] = {
                "trigger": "minecraft:player_killed_entity",
                "conditions": {"entity": {
                    "type": "minecraft:entity_properties",
                    "entity": "this",
                    "predicate": {"minecraft:entity_type": kill_obj["entity"]},
                }},
            }
        else:
            criteria["kill"] = {
                "trigger": "minecraft:player_killed_entity",
                "conditions": {"entity": [{"type": kill_obj["entity"]}]},
            }
    if talk_obj:
        criteria["talk"] = {
            "trigger": "minecraft:tick",
            "conditions": _player_conditions(mc, _tag("dma_obj", quest.id + "_talk")),
        }
    if rel_obj:
        criteria["relationship"] = {
            "trigger": "minecraft:tick",
            "conditions": _player_conditions(mc, _tag("dma_obj", quest.id + "_rel")),
        }
    if not criteria:
        criteria["done"] = {"trigger": "minecraft:impossible"}
    icon = npc.id if npc else "guide"
    return {
        "parent": f"{modid}:social/root",
        "display": {
            "icon": {_icon_key(mc): f"minecraft:emerald"},
            "title": {"translate": f"quest.{modid}.{quest.id}.title"},
            "description": {"translate": f"quest.{modid}.{quest.id}.description"},
            "frame": "goal",
            "show_toast": True,
            "announce_to_chat": False,
            "hidden": True,
        },
        "criteria": criteria,
        "requirements": [list(criteria.keys())],
        "rewards": {"function": f"{modid}:social/quest/{quest.id}/reward"},
    }


def generate_social(project: Path, spec: dict[str, Any], mc: str,
                    loader: str = "") -> dict[str, Any]:
    plan = build_plan(spec)
    modid = spec["identity"]["modid"]
    res = project / "src" / "main" / "resources"
    data_root = res / "data" / modid
    stats = {"npcs": len(plan.npcs), "quests": len(plan.quests),
             "dialogues": len(plan.dialogues), "functions": 0, "advancements": 0}
    if not plan.npcs:
        write_json(project / ".mcmod" / "social-manifest.json", {
            "schema": "mcmod-social-manifest/1", "mc": mc, "stats": stats,
        })
        return stats

    social_root = data_root / "dma"
    for npc in plan.npcs:
        write_json(social_root / "npc" / f"{npc.id}.json", {
            "id": f"{modid}:{npc.id}",
            "name": {"translate": f"entity.{modid}.{npc.id}"},
            "name_zh": npc.name_zh,
            "name_en": npc.name_en,
            "dialogue": f"{modid}:{npc.dialogue}",
            "marriageable": npc.marriageable,
            "schedule": npc.schedule,
            "quests": [f"{modid}:{q}" for q in npc.quests],
            "likes": npc.likes,
            "dislikes": npc.dislikes,
        })
    for dialogue_id, tree in plan.dialogues.items():
        write_json(social_root / "dialogue" / (dialogue_id + ".json"), {
            "speaker": f"{modid}:{tree['speaker']}",
            **tree,
        })
    for quest in plan.quests:
        write_json(social_root / "quest" / f"{quest.id}.json", {
            "id": f"{modid}:{quest.id}",
            "title": {"translate": f"quest.{modid}.{quest.id}.title"},
            "description": {"translate": f"quest.{modid}.{quest.id}.description"},
            "stage": quest.stage,
            "giver": f"{modid}:{quest.giver}",
            "objectives": quest.objectives,
            "rewards": quest.rewards,
            "requires": [f"{modid}:{r}" for r in quest.requires],
            "unlocks": [f"{modid}:{u}" for u in quest.unlocks],
            "persistence": "scoreboard+tags",
        })
    write_json(social_root / "settings.json", plan.settings)

    # Load / tick functions.
    load = ["# Created by mcmod-studio. Objectives are idempotent."]
    for npc in plan.npcs:
        load.append(f'scoreboard objectives add {_objective(npc.id)} dummy "{"关系:" + npc.name_zh}"')
    load.append('scoreboard objectives add dma_reputation dummy "城镇声望"')
    load.append("# Stage-one quests are always available to interact with.")
    for quest in plan.quests:
        if not quest.requires:
            load.append(f"# {quest.id} is a root quest")
    _write_function(res, mc, modid, "social/load", load)
    stats["functions"] += 1

    load_tag = res / "data" / "minecraft" / tag_dir(mc, "function") / "load.json"
    existing = {}
    try:
        existing = json.loads(load_tag.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    values = list(dict.fromkeys(list(existing.get("values", [])) + [f"{modid}:social/load"]))
    write_json(load_tag, {"replace": False, "values": values})

    # NPC dialogue functions.
    for npc in plan.npcs:
        root_lines = _speaker_line(npc.name_zh, plan.dialogues[f"{npc.id}/root"]["lines"][0])
        commands = [_tellraw(root_lines)]
        commands.append(_tellraw([_click_component("[1] 这里是什么地方？", f"/function {modid}:social/npc/{npc.id}/about", mc)]))
        commands.append(_tellraw([_click_component("[2] 有什么可以帮忙的？", f"/function {modid}:social/npc/{npc.id}/quests", mc)]))
        commands.append(_tellraw([_click_component("[3] 再见。", f"/function {modid}:social/npc/{npc.id}/close", mc)]))
        commands.append(_tellraw([_click_component("[4] 送一份礼物（消耗 1 个材料）", f"/function {modid}:social/npc/{npc.id}/gift", mc, "light_purple")]))
        commands.append(f"execute if score @s {_objective(npc.id)} matches 30.. run " +
                        _tellraw([_click_component("[5] 谈一谈我们的未来。", f"/function {modid}:social/npc/{npc.id}/marriage", mc, "light_purple")]))
        _write_function(res, mc, modid, f"social/npc/{npc.id}/root", commands)
        _write_function(res, mc, modid, f"social/npc/{npc.id}/about", [
            _tellraw(_speaker_line(npc.name_zh, npc.description or "我在这里生活了很久。")),
            _tellraw([_click_component("[返回]", f"/function {modid}:social/npc/{npc.id}/root", mc)]),
        ])
        quest_cmds = [_tellraw(_speaker_line(npc.name_zh, "最近的事情都写在这块板子上了。"))]
        for quest in plan.quests:
            if quest.giver != npc.id:
                continue
            quest_cmds.append(
                f"execute unless entity @s[tag={quest.done_tag}] run " +
                _tellraw([_click_component(f"接受委托：{quest.title}", f"/function {modid}:social/quest/{quest.id}/accept", mc)]))
        quest_cmds.append(_tellraw([_click_component("[返回]", f"/function {modid}:social/npc/{npc.id}/root", mc)]))
        _write_function(res, mc, modid, f"social/npc/{npc.id}/quests", quest_cmds)
        _write_function(res, mc, modid, f"social/npc/{npc.id}/close", [
            _tellraw(_speaker_line(npc.name_zh, "路上小心。")),
        ])
        _write_function(res, mc, modid, f"social/npc/{npc.id}/gift", [
            f"execute unless entity @s[tag=dma_gift_ready] run tag @s add dma_gift_ready",
            _tellraw(_speaker_line(npc.name_zh, "谢谢你的心意。")),
            f"scoreboard players add @s {_objective(npc.id)} 2",
            f"tellraw @s [{{\"text\":\"（{npc.name_zh}对你的好感 +2）\",\"color\":\"gray\"}}]",
        ])
        _write_function(res, mc, modid, f"social/npc/{npc.id}/marry", [
            _tellraw(_speaker_line(npc.name_zh, "那么，从今天起我们就是一家人了。")),
            f"scoreboard players set @s {_objective(npc.id)} 40",
            f"tag @s add {_tag('dma_spouse', npc.id)}",
            f"advancement grant @s only {modid}:social/marriage",
        ])
        stats["functions"] += 5

    # Quest functions + advancements.
    for quest in plan.quests:
        giver = next((n for n in plan.npcs if n.id == quest.giver), plan.npcs[0])
        accept = [
            f"execute if entity @s[tag={quest.done_tag}] run " + _tellraw([{"text": "这个委托已经完成了。", "color": "gray"}]),
            f"execute unless entity @s[tag={quest.done_tag}] run tag @s add {quest.unlocked_tag}",
            f"execute unless entity @s[tag={quest.done_tag}] run " +
            _tellraw(_speaker_line(giver.name_zh, f"委托已接受：{quest.title}")),
            f"execute unless entity @s[tag={quest.done_tag}] run " +
            _tellraw([{"text": quest.description, "color": "white"}]),
        ]
        _write_function(res, mc, modid, f"social/quest/{quest.id}/accept", accept)
        reward = [
            f"execute if entity @s[tag={quest.done_tag}] run return 0",
            f"tag @s add {quest.done_tag}",
            f"tag @s remove {quest.unlocked_tag}",
            f"tellraw @s [{{\"text\":\"委托完成：{quest.title}\",\"color\":\"gold\"}}]",
        ]
        for item in quest.rewards.get("items", [])[:3]:
            reward.append(f"give @s {item} 1")
        reward.append(f"experience add @s {int(quest.rewards.get('xp', 0))} points")
        for npc_id, value in (quest.rewards.get("relationship") or {}).items():
            reward.append(f"scoreboard players add @s {_objective(npc_id)} {int(value)}")
        for follow in quest.unlocks:
            reward.append(f"tag @s add {_tag('dma_unlocked', follow)}")
        _write_function(res, mc, modid, f"social/quest/{quest.id}/reward", reward)
        stats["functions"] += 2
        adv = _quest_advancement(quest, modid, mc, giver)
        write_json(data_root / data_dir(mc, "advancement") / "social" / "quest" / f"{quest.id}.json", adv)
        stats["advancements"] += 1

    # Quest board function: lists accepted/unlocked quests.
    board = [_tellraw([{"text": "=== 委托板 ===", "color": "gold"}])]
    for quest in plan.quests:
        board.append(
            f"execute unless entity @s[tag={quest.done_tag}] unless entity @s[tag={quest.unlocked_tag}] run " +
            _tellraw([_click_component(f"[可接取] {quest.title}", f"/function {modid}:social/quest/{quest.id}/accept", mc)]))
        board.append(
            f"execute if entity @s[tag={quest.unlocked_tag}] run " +
            _tellraw([{"text": f"[进行中] {quest.title}", "color": "yellow"}]))
        board.append(
            f"execute if entity @s[tag={quest.done_tag}] run " +
            _tellraw([{"text": f"[已完成] {quest.title}", "color": "dark_gray"}]))
    board.append(_tellraw([{"text": "任务目标会自动记录在进度界面。", "color": "gray"}]))
    _write_function(res, mc, modid, "social/board", board)
    stats["functions"] += 1

    # Social root advancement + marriage advancement.
    root_adv = data_root / data_dir(mc, "advancement") / "social" / "root.json"
    write_json(root_adv, {
        "display": {
            "icon": {_icon_key(mc): f"{modid}:{_path(spec['content'][0]['id'])}"},
            "title": {"translate": f"social.{modid}.root.title"},
            "description": {"translate": f"social.{modid}.root.description"},
            "background": "minecraft:textures/block/oak_planks.png",
            "frame": "task",
            "show_toast": False,
            "announce_to_chat": False,
            "hidden": True,
        },
        "criteria": {"tick": {"trigger": "minecraft:tick"}},
    })
    write_json(data_root / data_dir(mc, "advancement") / "social" / "marriage.json", {
        "parent": f"{modid}:social/root",
        "display": {
            "icon": {_icon_key(mc): "minecraft:golden_apple"},
            "title": {"translate": f"social.{modid}.marriage.title"},
            "description": {"translate": f"social.{modid}.marriage.description"},
            "frame": "challenge",
            "show_toast": True,
            "announce_to_chat": True,
            "hidden": True,
        },
        "criteria": {"tick": {"trigger": "minecraft:tick",
                              "conditions": _player_conditions(
                                  mc, _tag("dma_spouse", plan.npcs[0].id))}},
    })
    stats["advancements"] += 2

    write_json(project / ".mcmod" / "social-manifest.json", {
        "schema": "mcmod-social-manifest/1",
        "mc": mc,
        "loader": loader,
        "stats": stats,
        "plan": plan.to_dict(),
    })
    return stats
