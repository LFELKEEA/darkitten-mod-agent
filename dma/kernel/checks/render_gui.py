"""Rendering, particles, GUI and input checks (P2-12..24)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

GL_STATE = ("enableBlend", "disableBlend", "depthMask", "enableCull", "disableCull", "enableDepthTest", "disableDepthTest", "enableScissor", "disableScissor")


_PARTICLE_REGISTER = re.compile(r"registerSpriteSet\s*\(\s*([\w.]+)\s*\.\s*get\s*\(")


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_duplicate_particle_registrations(ctx))
    rows.extend(_flat_textures(ctx))
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        renderer = any(key in java_class.name for key in ("Renderer", "Render", "Hud", "HUD", "Screen", "Menu", "Particle"))
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            if method.name in {"render", "renderGui", "renderHud", "draw", "renderBackground"}:
                if "new BufferBuilder(" in body or "Tesselator.getInstance().begin" in body:
                    if "RenderType." in body and method.name == "render":
                        rows.append(finding(
                            "P2-12", f"{java_class.name}.{method.name} 每个实体/每帧新建 RenderType/Buffer",
                            path=rel, line=method.line, severity="warning",
                            suggestion="复用 RenderType 与顶点缓冲，按材质批渲染。",
                        ))
                if "for (" in body and "RenderType." in body:
                    rows.append(finding(
                        "P2-12", f"{java_class.name}.{method.name} 在循环内切换 RenderType",
                        path=rel, line=method.line, severity="warning",
                        suggestion="先按 RenderType 分组，再提交绘制。",
                    ))
                if any(state in body for state in GL_STATE):
                    enabled = sum(body.count(f"enable{state[6:]}") for state in GL_STATE)
                    disabled = sum(body.count(f"disable{state[6:]}") for state in GL_STATE)
                    if enabled and not disabled and "try" not in body and "finally" not in body:
                        rows.append(finding(
                            "P2-13", f"{java_class.name}.{method.name} 修改 GL 状态但没有恢复",
                            path=rel, line=method.line, severity="warning",
                            suggestion="用 try/finally 或 push/pop 状态栈，渲染结束恢复 Blend/Depth/Cull。",
                        ))
                if any(key in java_class.name for key in ("Hud", "HUD", "Overlay")):
                    if not any(key in body for key in ("hideGui", "options.hideGui", "screen != null", "getScreen()")):
                        rows.append(finding(
                            "P2-24", f"{java_class.name} 的 HUD 绘制没有暂停/打开 GUI 的守卫",
                            path=rel, line=method.line, severity="warning",
                            suggestion="在暂停、打开 GUI、隐藏 HUD 时不绘制，并校验渲染层级。",
                        ))
            if "BlockEntityRenderer" in (java_class.extends or "") and method.name == "render":
                if re.search(r"blockEntity\.\w+\s*=", body) or "setChanged()" in body:
                    rows.append(finding(
                        "P2-16", f"{java_class.name} 在渲染线程写 BlockEntity",
                        path=rel, line=method.line,
                        suggestion="渲染只用不可变快照；状态修改放服务端/主线程。",
                    ))
            if "shadow" in method.name.lower() or "renderNameTag" in method.name:
                continue
        # FrameBuffer / RenderTarget lifecycle
        if "FrameBuffer" in text or "RenderTarget" in text or "PostChain" in text:
            if "destroy" not in text and "close()" not in text:
                rows.append(finding(
                    "P2-17", f"{java_class.name} 创建 FBO/RenderTarget 但没有销毁逻辑",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="在 resize/关闭时销毁并重建，避免显存泄漏与花屏。",
                ))
            if "resize" not in text and "onResize" not in text:
                rows.append(finding(
                    "P2-17", f"{java_class.name} 没有处理窗口缩放",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="监听 resize 并重建渲染目标。",
                ))
        # Particles
        # addParticle 的寿命与可视距离由粒子引擎控制；animateTick /
        # handleEntityEvent / isClientSide 分支里的调用都是标准写法。
        # 原规则逐类要求 setLifetime/distanceToSqr，只会制造误报，已移除。
        # Menu contracts
        if "AbstractContainerMenu" in (java_class.extends or "") or "ScreenHandler" in (java_class.extends or ""):
            if "quickMoveStack" not in text and "quickMove" not in text:
                rows.append(finding(
                    "P2-20", f"{java_class.name} 没有实现 quickMoveStack，Shift 点击会失效",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="实现槽位转移规则，并在服务端校验槽位修改。",
                ))
            if "stillValid" not in text and "canUse" not in text:
                rows.append(finding(
                    "P2-20", f"{java_class.name} 没有 stillValid/canUse，玩家走远后菜单仍然可用",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="校验玩家距离与方块状态；服务端拒绝非法槽位操作。",
                ))
            if "removed(" not in text and "onClose" not in text and "closeContainer" not in text:
                rows.append(finding(
                    "P2-21", f"{java_class.name} 没有关闭/离线清理钩子",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="覆盖 removed/onClose，玩家下线/传送时释放状态。",
                ))
        if "Screen" in (java_class.extends or "") or "Screen" in java_class.name:
            if "keyPressed" in text and "return false;" in text and "super.keyPressed" not in text:
                rows.append(finding(
                    "P2-23", f"{java_class.name}.keyPressed 总是返回 false，可能抢焦点/丢按键",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="未处理时调用 super，文本框聚焦时不要捕获按键。",
                ))
            if "mouseClicked" in text and ("getGuiScaledWidth" not in text and "getGuiScaledHeight" not in text):
                rows.append(finding(
                    "P2-23", f"{java_class.name} 直接使用鼠标坐标，没有 GUI 缩放换算",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="用 getGuiScaledWidth/Height 与缩放因子换算，适配高 DPI。",
                ))
            if "renderTooltip" in text and "split" not in text and "\\n" in text:
                rows.append(finding(
                    "P2-22", "Tooltip 使用硬编码换行，长文本会溢出",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="用 Component 的换行/滚动组件，按屏幕宽度截断。",
                ))
    # Shader validation
    for path in Path(ctx.root).rglob("*"):
        if path.suffix.lower() not in {".vsh", ".fsh", ".glsl", ".json"}:
            continue
        if "shader" not in path.as_posix().lower():
            continue
        if path.suffix.lower() == ".json":
            continue
        text = ctx.read(path)
        if "#version" not in text:
            rows.append(finding(
                "P2-14", "着色器缺少 #version，部分驱动会编译失败",
                path=ctx.rel(path),
                suggestion="显式声明 #version 150（或与 loader 模板一致）。",
            ))
    for java in ctx.java_files:
        if "ShaderInstance" in java.text and "try" not in java.text:
            rows.append(finding(
                "P2-14", "加载着色器没有异常降级",
                path=ctx.rel(java.path), severity="warning",
                suggestion="编译失败时回退到默认材质并记录日志，避免黑屏。",
            ))
    rows.extend(_model_layers(ctx))
    rows.extend(_humanoid_outer_layers(ctx))
    return rows


# 字段声明可能跨行，第一个参数里还嵌着 ResourceLocation(...)，按“到分号
# 为止的整段”取，再从中提取字符串字面量，避免被内层逗号打断。
_LAYER_FIELD = re.compile(
    r"\b(\w+)\s*=\s*new\s+ModelLayerLocation\s*\((.*?)\)\s*;", re.S
)
_STRING_LITERAL = re.compile(r"\"([\w_./-]+)\"")


_HUMANOID_OUTER = (
    "hat", "jacket", "left_sleeve", "right_sleeve", "left_pants", "right_pants",
)
_HUMANOID_BASE = ("head", "body", "right_arm", "left_arm", "right_leg", "left_leg")
_REPLACE_CHILD = re.compile(r"\.addOrReplaceChild\s*\(\s*\"(\w+)\"")


def _flat_textures(ctx: ProjectContext) -> list[Finding]:
    """贴图质量：纯色块 / 全黑实体在游戏里就是一团看不懂的色块。

    只看 PNG 像素，不做主观判断：颜色数量、主色占比、近黑像素占比。
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - 没有 Pillow 时跳过，不影响其它检查
        return []
    assets = Path(ctx.root) / "src" / "main" / "resources" / "assets"
    if not assets.is_dir():
        return []
    rows: list[Finding] = []
    for png in sorted(assets.glob("*/textures/**/*.png")):
        rel = ctx.rel(png)
        parts = png.parts
        group = ""
        for name in ("entity", "item", "block"):
            if name in parts:
                group = name
                break
        if not group:
            continue
        try:
            image = Image.open(png).convert("RGBA")
        except Exception:  # noqa: BLE001 - 坏图由资源校验负责
            continue
        width, height = image.size
        pixels = list(image.getdata())
        painted = [px for px in pixels if px[3] > 16]
        if len(painted) < 16:
            continue
        buckets: dict[tuple[int, int, int], int] = {}
        dark = 0
        xs: list[int] = []
        ys: list[int] = []
        for index, (r, g, b, a) in enumerate(pixels):
            if a <= 16:
                continue
            xs.append(index % width)
            ys.append(index // width)
            key = (r // 16, g // 16, b // 16)
            buckets[key] = buckets.get(key, 0) + 1
            if max(r, g, b) < 40:
                dark += 1
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        total = len(painted)
        colors = len(buckets)
        dominant = max(buckets.values()) / total
        dark_ratio = dark / total
        coverage = total / max(1, width * height)
        if group == "entity":
            if width < 64 or height < 64:
                rows.append(finding(
                    "P2-15",
                    f"实体贴图 {png.name} 尺寸只有 {width}x{height}，"
                    "原版实体 UV 至少需要 64x64，直接贴图会被拉伸成糊块",
                    path=rel, line=1, severity="warning",
                    suggestion="用 entity_texture 重新生成 64x64 UV 图集，"
                               "不要用 texture_gen 的物品模板画实体皮肤。",
                ))
            # 物品模板的特征：图只画在画布正中，四周留出一圈空白。
            # 真实的实体 UV 图集一定从 (0,0) 开始铺，不可能四周都空。
            centered = (x0 >= 8 and y0 >= 8 and x1 <= width - 8 and y1 <= height - 8)
            if centered and coverage < 0.75:
                rows.append(finding(
                    "P2-15",
                    f"实体贴图 {png.name} 只画在画布中间（({x0},{y0})-({x1},{y1})），"
                    "四周留白，是物品模板而不是实体 UV 图集",
                    path=rel, line=1, severity="error",
                    suggestion="用 entity_texture 按 humanoid 或 boxes 从 (0,0) 开始铺 UV；"
                               "物品模板只画中间一小块，模型全身都会被拉成糊块。",
                ))
            if dark_ratio >= 0.22:
                rows.append(finding(
                    "P2-15",
                    f"实体贴图 {png.name} 有 {dark_ratio:.0%} 像素接近纯黑，"
                    "进游戏是一团黑影，看不清轮廓和部位",
                    path=rel, line=1, severity="error",
                    suggestion="用 entity_texture 重新生成：主色亮度不要低于 #4a4a5a，"
                               "按面做明暗（顶亮、底暗、侧面中间调），眼睛/细节用高光色。",
                ))
            elif colors <= 3:
                rows.append(finding(
                    "P2-15",
                    f"实体贴图 {png.name} 只有 {colors} 种颜色，等于把模型涂成色块",
                    path=rel, line=1, severity="error",
                    suggestion="用 entity_texture 生成带噪点、明暗过渡和面部细节的 UV 图集。",
                ))
            elif dominant >= 0.72:
                rows.append(finding(
                    "P2-15",
                    f"实体贴图 {png.name} 单一颜色占 {dominant:.0%}，缺少材质层次",
                    path=rel, line=1, severity="warning",
                    suggestion="加大明暗跨度，或加腰饰/披风/斑纹，避免整只生物一个颜色。",
                ))
        elif group == "item":
            if colors <= 3 and width <= 32 and height <= 32:
                rows.append(finding(
                    "P2-15",
                    f"物品贴图 {png.name} 只有 {colors} 种颜色，看起来是纯色方块",
                    path=rel, line=1, severity="error",
                    suggestion="用 texture_gen 的 item 模式重新生成：外描边 + 高光 + 阴影 + 点缀色。",
                ))
            elif dominant >= 0.72:
                rows.append(finding(
                    "P2-15",
                    f"物品贴图 {png.name} 单色占 {dominant:.0%}，没有形状细节",
                    path=rel, line=1, severity="warning",
                    suggestion="重新生成时加轮廓与倒角，材质区用两到三阶明暗。",
                ))
        else:
            if colors <= 1:
                rows.append(finding(
                    "P2-15",
                    f"方块贴图 {png.name} 是纯色块，铺满世界会非常廉价",
                    path=rel, line=1, severity="warning",
                    suggestion="用 texture_gen 的 stone/dirt/planks/leaves 等样式重新生成。",
                ))
    return rows


def _duplicate_particle_registrations(ctx: ProjectContext) -> Iterable[Finding]:
    """同一个粒子类型被两个客户端事件处理器重复注册。

    Forge 的 ``RegisterParticleProvidersEvent.registerSpriteSet`` 会为每次
    调用创建一个 MutableSpriteSet，只有最后一个被 ParticleEngine 记住并
    在资源重载时填充。被覆盖的那份 sprite 列表一直是 null，粒子一旦在
    世界里生成就抛 ``Cannot invoke List.size() because sprites is null``，
    客户端直接崩溃——而且往往是在进世界几十秒后才炸，很难定位。
    """
    rows: list[Finding] = []
    seen: dict[str, list[tuple[str, int]]] = {}
    for java in ctx.java_files:
        for match in _PARTICLE_REGISTER.finditer(java.text):
            expr = match.group(1)
            line = java.text.count("\n", 0, match.start()) + 1
            seen.setdefault(expr, []).append((ctx.rel(java.path), line))
    for expr, places in seen.items():
        if len(places) < 2:
            continue
        first_path, first_line = places[0]
        others = "、".join(f"{path}:{line}" for path, line in places[1:])
        rows.append(finding(
            "P2-15",
            f"粒子 {expr} 被重复注册 {len(places)} 次（{first_path}:{first_line}；{others}）。"
            "Forge 只填充最后一次注册的 MutableSpriteSet，其余实例的 sprite 列表始终为 null，"
            "生成粒子时会 NPE 并让客户端崩溃",
            path=first_path, line=first_line, severity="error",
            suggestion="只保留一个客户端事件处理器注册粒子；删除重复的 "
                       "RegisterParticleProvidersEvent 处理器或合并到同一个类。",
        ))
    return rows


def _humanoid_outer_layers(ctx: ProjectContext) -> Iterable[Finding]:
    """自定义人形模型不能沿用 HumanoidModel.createMesh 的默认外层。

    createMesh 会顺带建好 hat/jacket/sleeve/pants 这些“皮肤外层”，UV 全部按
    原版玩家皮肤布局写死。模型换了自定义贴图后，这些层仍会去采样贴图上的
    其他位置，渲染出来就是模型外面套着一圈彩色乱码块。
    """
    rows: list[Finding] = []
    for java in ctx.java_files:
        for java_class in java.all_classes():
            if "HumanoidModel" not in (java_class.extends or ""):
                continue
            body = java_class.body
            if "HumanoidModel.createMesh" not in body:
                continue
            replaced = set(_REPLACE_CHILD.findall(body))
            # 至少重写了 3 个基础部位，才说明这是自定义体型而不是原版皮肤。
            if len(replaced & set(_HUMANOID_BASE)) < 3:
                continue
            # 自己重写 renderToBuffer 且不调用 super 时，外层不会被渲染。
            if "renderToBuffer" in body and "super.renderToBuffer" not in body:
                continue
            missing = [name for name in _HUMANOID_OUTER if name not in replaced]
            if not missing:
                continue
            rows.append(finding(
                "P1-12",
                f"{java_class.name} 继承 HumanoidModel 但保留了默认外层 "
                f"{'、'.join(missing)}，会按原版皮肤 UV 采样自定义贴图，"
                "模型外表面渲染成彩色乱码块",
                path=ctx.rel(java.path), line=java_class.line, severity="error",
                suggestion="对每个未使用的外层执行 "
                           "root.addOrReplaceChild(\"hat\", CubeListBuilder.create(), "
                           "PartPose.ZERO)（jacket/sleeve/pants 同理）清空，"
                           "或重写 renderToBuffer 只渲染自己的部件。",
            ))
    return rows


def _model_layers(ctx: ProjectContext) -> Iterable[Finding]:
    """bakeLayer 用到的模型层必须先在 RegisterLayerDefinitions 里注册。

    缺一层，客户端在实体渲染器初始化时抛
    IllegalArgumentException: No model for layer ...，整个客户端打不开。
    """
    identities: dict[tuple[str, str], str] = {}
    for java in ctx.java_files:
        if "ModelLayerLocation" not in java.text:
            continue
        for java_class in java.all_classes():
            for match in _LAYER_FIELD.finditer(java.text):
                literals = _STRING_LITERAL.findall(match.group(2))
                if len(literals) >= 2:
                    # 例如 new ModelLayerLocation(TwilightGrove.id("firefly"), "main")
                    identities[(java_class.name, match.group(1))] = (
                        f"{literals[-2]}#{literals[-1]}"
                    )

    def resolve(expr: str, owner: str) -> str:
        expr = expr.strip()
        if "." in expr:
            klass, _, field = expr.rpartition(".")
            return identities.get((klass, field), "")
        return identities.get((owner, expr), "")

    registered: set[str] = set()
    used: list[tuple[str, str, int]] = []
    seen_used: set[str] = set()
    for java in ctx.java_files:
        text = java.text
        for java_class in java.all_classes():
            for match in re.finditer(r"registerLayerDefinition\s*\(\s*([\w.$]+)", text):
                key = resolve(match.group(1), java_class.name)
                if key:
                    registered.add(key)
            for match in re.finditer(r"bakeLayer\s*\(\s*([\w.$]+)\s*\)", text):
                key = resolve(match.group(1), java_class.name)
                if key and key not in seen_used:
                    seen_used.add(key)
                    used.append((
                        key, ctx.rel(java.path),
                        text.count("\n", 0, match.start()) + 1,
                    ))

    rows: list[Finding] = []
    for key, rel, line in used:
        if key in registered:
            continue
        rows.append(finding(
            "P1-12",
            f"模型层 {key} 被 bakeLayer 使用，但没有 registerLayerDefinition，客户端会崩溃",
            path=rel, line=line, severity="error",
            suggestion="在 EntityRenderersEvent.RegisterLayerDefinitions 里补 "
                       "registerLayerDefinition(LAYER, Model::createBodyLayer)。",
        ))
    return rows
