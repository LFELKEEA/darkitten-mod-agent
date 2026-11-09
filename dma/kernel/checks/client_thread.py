"""Client-only references and async world writes (P0-08 / P0-09 / P2-57/58)."""

from __future__ import annotations

import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

CLIENT_TYPES = (
    "net.minecraft.client.",
    "com.mojang.blaze3d.",
    "MinecraftClient",
    "Minecraft.getInstance",
    "GuiGraphics",
    "DrawContext",
    "RenderSystem",
    "KeyMapping",
    "ParticleEngine",
    "TextureManager",
    "EntityRenderDispatcher",
    "BlockEntityRenderer",
    "ItemRenderer",
    "GameRenderer",
    "LevelRenderer",
    "SoundEngine",
    "ToastManager",
    "MouseHandler",
    "KeyboardHandler",
)

ASYNC_MARKERS = (
    "CompletableFuture", "supplyAsync", "runAsync", "ExecutorService",
    "Executors.new", "new Thread", "ForkJoinPool", "parallelStream",
    "ThreadPoolExecutor", "ScheduledExecutorService", "Schedulers.",
)

WORLD_WRITES = (
    "setBlock", "setBlockAndUpdate", "setBlockState", "addFreshEntity",
    "spawnEntity", "removeBlock", "destroyBlock", "setData", "sendBlockUpdated",
    "updateNeighborsAt", "neighborChanged", "playSound", "broadcastEntityEvent",
    "addEffect", "hurt", "discard", "remove", "tickBlock", "scheduleTick",
    "setChunkForced", "addParticle", "setSpawnPos", "setWeatherParameters",
)

MAIN_THREAD_HOPS = (
    "server.execute", "getServer().execute", "Minecraft.getInstance().execute",
    "enqueueWork", "ctx.enqueueWork", "mainThread", "runOnMainThread",
    "ServerLifecycleHooks.getCurrentServer().execute",
)


def _has(annotation_text: str, name: str) -> bool:
    return f"@{name}" in annotation_text or f".{name}" in annotation_text


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []

    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        annotation_text = " ".join(java_class.annotations)
        client_annotated = any(
            key in annotation_text
            for key in ("OnlyIn", "Environment", "Dist.CLIENT", "EnvType.CLIENT")
        )
        in_client_dir = "/client/" in f"/{rel}" or "/client/" in rel.replace("\\", "/")
        if ctx.is_common_path(rel) and not client_annotated and not in_client_dir:
            body = java.masked
            hits = [token for token in CLIENT_TYPES if token in body]
            if hits:
                # A guarded reference is downgraded, not ignored.
                guarded = any(marker in body for marker in (
                    "DistExecutor", "FMLEnvironment.dist", "EnvType.CLIENT", "isClientSide",
                ))
                rows.append(finding(
                    "P0-08",
                    f"公共代码引用了客户端专属类型：{', '.join(hits[:4])}",
                    path=rel, line=_first_line(body, hits[0]),
                    severity="warning" if guarded else "error",
                    suggestion="移入 client 包，或加 @OnlyIn(Dist.CLIENT)/@Environment(EnvType.CLIENT)，并用 DistExecutor 隔离调用。",
                ))
            if "Minecraft.getInstance()" in body and not guarded:
                rows.append(finding(
                    "P0-08", "公共初始化路径直接调用 Minecraft.getInstance()",
                    path=rel, line=_first_line(body, "Minecraft.getInstance()"),
                    suggestion="通过客户端事件或 DistExecutor 延迟到客户端侧执行。",
                ))

        for method in java_class.methods:
            body = method.body
            if not body.strip():
                continue
            async_hit = next((marker for marker in ASYNC_MARKERS if marker in body), "")
            if async_hit:
                writes = [name for name in WORLD_WRITES if re.search(rf"\.{re.escape(name)}\s*\(", body)]
                guarded = any(marker in body for marker in MAIN_THREAD_HOPS)
                if writes and not guarded:
                    rows.append(finding(
                        "P0-09",
                        f"异步分支（{async_hit}）直接写世界：{', '.join(writes[:4])}",
                        path=rel, line=method.line,
                        suggestion="把写操作提交回主线程（server.execute / enqueueWork），异步线程只做只读计算。",
                    ))
                if "getChunk(" in body and not guarded:
                    rows.append(finding(
                        "P2-58", "异步分支读取区块，缺少主线程对齐或读写锁",
                        path=rel, line=method.line, severity="warning",
                        suggestion="区块读写必须回到主线程，或使用明确的读写锁并避免跨 tick 持有。",
                    ))
            if "new LinkedList" in body or "ConcurrentLinkedQueue" in body or "ArrayDeque" in body:
                if "size()" not in body and "offer(" in body and "poll(" in body:
                    rows.append(finding(
                        "P2-57", "自建任务队列没有长度上限，堆积后会拖慢主线程",
                        path=rel, line=method.line, severity="warning",
                        suggestion="设置最大长度与丢弃/合并策略，并在 tick 内限制处理数量。",
                    ))
            if "Thread.sleep(" in body and any(key in rel for key in ("/server", "Tick", "tick")):
                rows.append(finding(
                    "P2-57", "在服务端/tick 路径中调用 Thread.sleep",
                    path=rel, line=method.line, severity="warning",
                    suggestion="改为调度任务或异步等待，绝不阻塞主线程。",
                ))
            if "parallelStream(" in body and any(
                key in body for key in ("getEntities", "getPlayers", "getChunk", "Level", "World")
            ):
                rows.append(finding(
                    "P2-58", "对世界/实体集合使用 parallelStream，存在并发读写风险",
                    path=rel, line=method.line, severity="warning",
                    suggestion="串行遍历或先复制只读快照，再在主线程应用结果。",
                ))
    return rows


def _first_line(text: str, token: str) -> int:
    index = text.find(token)
    return text.count("\n", 0, index) + 1 if index >= 0 else 0
