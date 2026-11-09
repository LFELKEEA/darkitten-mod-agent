"""Network packet rules (P0-10 / P0-11 / P2-27..33)."""

from __future__ import annotations

import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

PACKET_HINTS = ("FriendlyByteBuf", "CustomPacketPayload", "Packet<", "Packet ", "StreamCodec", "PacketByteBuf")
VALIDATION_HINTS = (
    "validate", "check", "clamp", "distanceToSqr", "distanceTo", "hasPermission",
    "isOp", "canUse", "abort", "disconnect", "isAlive", "getServer", "ServerPlayer",
    "permission", "max(", "min(", "MathHelper", "Mth.", "limit",
)
WORLD_MUTATION = (
    "setPos(", "teleportTo(", "hurt(", "damage(", "addItem(", "setItem(",
    "setBlock(", "setBlockAndUpdate(", "addFreshEntity(", "spawnEntity(",
    "setHealth(", "setGameMode(", "giveExperience(", "addEffect(",
)
SEND_TO_SERVER = ("sendToServer", "send(PacketDistributor.SERVER", "ServerPlayNetworking.send")


def _looks_like_packet(java: object, java_class: object) -> bool:
    text = getattr(java, "text", "")
    name = getattr(java_class, "name", "")
    body = getattr(java_class, "body", "")
    return (
        any(hint in text for hint in PACKET_HINTS)
        and ("Packet" in name or "Payload" in name or "FriendlyByteBuf" in body or "StreamCodec" in body)
    )


def _direction(name: str, rel: str) -> str:
    value = f"{name} {rel}".lower()
    if any(key in value for key in ("serverbound", "c2s", "toserver", "server_packet")):
        return "serverbound"
    if any(key in value for key in ("clientbound", "s2c", "toclient", "client_packet")):
        return "clientbound"
    return ""


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    packet_classes = 0
    has_protocol_version = False
    has_handshake = False
    has_logout_cleanup = False

    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        body = java_class.body
        if not _looks_like_packet(java, java_class):
            continue
        packet_classes += 1
        name = java_class.name
        direction = _direction(name, rel)
        text = java.text
        if "PROTOCOL_VERSION" in text or "protocolVersion" in text or "protocol_version" in text:
            has_protocol_version = True
        if "handshake" in text.lower() or "negotiat" in text.lower():
            has_handshake = True
        if any(key in text for key in ("PlayerLoggedOutEvent", "DISCONNECT", "onDisconnect", "playerLoggedOut")):
            has_logout_cleanup = True

        if not any(key in text for key in ("PROTOCOL_VERSION", "protocolVersion", "protocol_version", "version")):
            rows.append(finding(
                "P0-10", f"网络包 {name} 没有协议版本标记",
                path=rel, line=java_class.line,
                suggestion="在通道注册时写入并校验 PROTOCOL_VERSION；不匹配时友好拒绝而不是直接断连。",
            ))
        if not any(key in text for key in ("RateLimiter", "rateLimit", "cooldown", "throttle", "lastSent", "lastPacket", "timestamp")):
            rows.append(finding(
                "P0-10", f"网络包 {name} 未发现限流/冷却逻辑",
                path=rel, line=java_class.line, severity="warning",
                suggestion="按玩家/包类型限流，超限丢弃并记录，避免刷包拖垮服务端。",
            ))
        if re.search(r"byte\[\]\s+\w+|List<Byte>|ByteBuf", body) and not any(
            key in text for key in ("chunk", "split", "fragment", "MAX_", "maxSize", "limit")
        ):
            rows.append(finding(
                "P0-10", f"网络包 {name} 可能携带大块数据但没有分片/大小上限",
                path=rel, line=java_class.line, severity="warning",
                suggestion="限制单包大小，超出时按块编号分片并在接收端重组。",
            ))

        annotated_handler = False
        for method in java_class.methods:
            handler_body = method.body
            if not handler_body.strip():
                continue
            annotation_text = " ".join(method.annotations)
            is_handler = any(
                key in annotation_text
                for key in ("SubscribeEvent", "PacketHandler", "Handler", "handle")
            ) or method.name.lower().startswith(("handle", "on", "receive"))
            if not is_handler:
                continue
            annotated_handler = True
            if any(key in handler_body for key in ("Minecraft.getInstance()", "MinecraftClient.getInstance()", "level.", "world.")):
                if "enqueueWork" not in handler_body and ".execute(" not in handler_body:
                    rows.append(finding(
                        "P0-10", f"包处理器 {method.name} 在 Netty 线程直接访问客户端/世界状态",
                        path=rel, line=method.line,
                        suggestion="用 ctx.enqueueWork(() -> ...) 切回主线程，禁止在 Netty 线程碰世界。",
                    ))
            if direction == "serverbound" or "Server" in name:
                writes = [token for token in WORLD_MUTATION if token in handler_body]
                if writes:
                    validated = any(token in handler_body for token in VALIDATION_HINTS)
                    if not validated:
                        rows.append(finding(
                            "P0-11",
                            f"服务端包 {name} 直接应用客户端数据（{', '.join(writes[:3])}）且未见权威校验",
                            path=rel, line=method.line,
                            suggestion="服务端重新计算坐标/伤害/数量，校验距离、权限、存活状态；客户端值只作意图提示。",
                        ))
            if direction == "clientbound" and any(token in handler_body for token in SEND_TO_SERVER):
                rows.append(finding(
                    "P0-10", f"客户端包 {name} 在客户端侧调用 sendToServer，方向可能是反的",
                    path=rel, line=method.line, severity="warning",
                    suggestion="Clientbound 包只负责下发；客户端上行使用 Serverbound 包。",
                ))

        if not annotated_handler and direction in {"serverbound", "clientbound"}:
            rows.append(finding(
                "P0-10", f"网络包 {name} 没有可识别的处理器方法",
                path=rel, line=java_class.line, severity="warning",
                suggestion="显式实现 handle/Handler，并区分线程与方向。",
            ))
        if "saveWithoutMetadata" in body or "save(" in body and "CompoundTag" in body:
            rows.append(finding(
                "P2-27", f"网络包 {name} 可能整包同步 NBT",
                path=rel, line=java_class.line, severity="warning",
                suggestion="只同步脏字段，带版本号/序列号，避免带宽爆炸和旧包覆盖新状态。",
            ))
        if any(key in body for key in ("CompoundTag", "NbtCompound")) and not any(
            key in text for key in ("seq", "sequence", "nonce", "revision", "dirty")
        ):
            rows.append(finding(
                "P2-28", f"网络包 {name} 没有序列号/版本号，重复包和乱序包无法识别",
                path=rel, line=java_class.line, severity="warning",
                suggestion="加入单调递增序号与幂等处理，旧序号包直接丢弃。",
            ))
        if direction == "clientbound" and any(
            key in body for key in ("setBlock", "setPos", "teleportTo")
        ) and "Rollback" not in text and "confirm" not in text.lower():
            rows.append(finding(
                "P2-29", f"客户端预测 {name} 未见回滚/确认逻辑",
                path=rel, line=java_class.line, severity="warning",
                suggestion="服务端拒绝时清理预测状态；移动类预测要平滑回滚，避免抖动和瞬移。",
            ))
        if "setBlock" in body and direction == "clientbound" and not any(
            key in text for key in ("Rollback", "reject", "clearPrediction")
        ):
            rows.append(finding(
                "P2-30", f"客户端预放置 {name} 没有失败清除逻辑，可能产生幽灵方块",
                path=rel, line=java_class.line, severity="warning",
                suggestion="服务端确认后再落地；拒绝时恢复旧 BlockState。",
            ))

    if packet_classes:
        if not has_protocol_version:
            rows.append(finding(
                "P0-10", "项目里有自定义网络包，但没有任何协议版本常量",
                suggestion="统一维护 PROTOCOL_VERSION，登录握手时协商。",
            ))
        if not has_handshake:
            rows.append(finding(
                "P2-32", "自定义网络通道未见握手/版本协商",
                severity="warning",
                suggestion="在登录阶段交换协议版本，不匹配时给出可读提示。",
            ))
        if not has_logout_cleanup:
            rows.append(finding(
                "P2-31", "未见玩家登出/切维度清理逻辑",
                severity="warning",
                suggestion="在 PlayerLoggedOutEvent / DISCONNECT 中清理玩家队列、缓存和待发包。",
            ))
        if not any("setCompressionThreshold" in java.text or "compression" in java.text.lower() for java in ctx.java_files):
            rows.append(finding(
                "P2-33", "未见压缩阈值/上下文重置处理",
                severity="info",
                suggestion="保持客户端/服务端压缩配置一致，切换维度或重连后重置上下文。",
            ))
    return rows
