"""Rendering, particles, GUI and input checks (P2-12..24)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

GL_STATE = ("enableBlend", "disableBlend", "depthMask", "enableCull", "disableCull", "enableDepthTest", "disableDepthTest", "enableScissor", "disableScissor")


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
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
        if "addParticle" in text:
            if "setLifetime" not in text and "lifetime" not in text and "ParticleOptions" not in text:
                rows.append(finding(
                    "P2-19", f"{java_class.name} 生成粒子但没有生命周期控制",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="设置寿命并做距离裁剪；绑定实体的粒子随实体移除。",
                ))
            if "distanceToSqr" not in text and "distance" not in text and "within" not in text:
                rows.append(finding(
                    "P2-19", f"{java_class.name} 粒子生成没有距离裁剪",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="超出可视距离不生成，降低客户端压力。",
                ))
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
    return rows
