package com.mcdev.probe.mixin;

import com.mcdev.probe.McdevProbe;
import com.mcdev.probe.StartupWindow;
import com.mojang.blaze3d.platform.Window;
import org.lwjgl.glfw.GLFW;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.Redirect;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import java.util.function.IntSupplier;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/**
 * 后台模式下让主窗口从创建开始就不出现在桌面。
 *
 * 三层处理都在渲染线程上完成，不依赖外部轮询：
 * 1. 每次 glfwWindowHint 后补 GLFW_VISIBLE=false；
 * 2. setupMinecraftWindow 返回句柄的瞬间立即隐藏；
 * 3. 构造器结束时再确认一次。
 *
 * 只在客户端生效（mixins.json 的 client 段）；服务端没有这个类。
 */
@Mixin(Window.class)
public abstract class WindowMixin {

    /**
     * 句柄一返回就隐藏，抢在 positionWindow / makeContextCurrent 之前。
     *
     * 某些窗口提供方会忽略 GLFW_VISIBLE 提示，窗口仍会短暂映射到桌面；
     * 这里包住 setupMinecraftWindow，拿到 long 句柄的第一时间调用
     * glfwHideWindow，把可见时间压到窗口创建本身的那几毫秒。
     */
    @Redirect(
            method = "<init>",
            at = @At(
                    value = "INVOKE",
                    target = "Lnet/minecraftforge/fml/loading/ImmediateWindowHandler;"
                            + "setupMinecraftWindow(Ljava/util/function/IntSupplier;"
                            + "Ljava/util/function/IntSupplier;Ljava/util/function/Supplier;"
                            + "Ljava/util/function/LongSupplier;)J"
            ),
            remap = false
    )
    private long mcdev$hideRightAfterCreate(
            IntSupplier width,
            IntSupplier height,
            Supplier<String> title,
            LongSupplier monitor
    ) {
        long handle = net.minecraftforge.fml.loading.ImmediateWindowHandler
                .setupMinecraftWindow(width, height, title, monitor);
        if (handle != 0L && StartupWindow.backgroundRequested()) {
            GLFW.glfwHideWindow(handle);
        }
        return handle;
    }

    /**
     * Window 构造器里每次 glfwWindowHint 之后补一条 GLFW_VISIBLE=false。
     *
     * Mixin 0.8.5 禁止在构造器里用 @At("INVOKE") 的 @Inject（METHODS_ONLY），
     * 但 @Redirect 走的是另一条注入路径，可以改写这里的静态调用。这样
     * glfwCreateWindow 拿到的可见性就是 false，窗口不会在桌面上映射出来。
     */
    @Redirect(
            method = "<init>",
            at = @At(
                    value = "INVOKE",
                    target = "Lorg/lwjgl/glfw/GLFW;glfwWindowHint(II)V"
            ),
            remap = false
    )
    private void mcdev$forceInvisibleHint(int target, int hint) {
        GLFW.glfwWindowHint(target, hint);
        if (StartupWindow.backgroundRequested()) {
            GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE, GLFW.GLFW_FALSE);
        }
    }

    @Inject(method = "<init>", at = @At("RETURN"), remap = false)
    private void mcdev$hideAtCreation(CallbackInfo ci) {
        if (!StartupWindow.backgroundRequested()) {
            return;
        }
        try {
            Window self = (Window) (Object) this;
            long handle = self.getWindow();
            if (handle != 0L) {
                GLFW.glfwHideWindow(handle);
                McdevProbe.LOGGER.info(
                        "Mcdev 主窗口创建后确认隐藏（GLFW 句柄 {}）", handle);
            }
        } catch (Throwable t) {
            // 隐藏失败不影响游戏启动，外部的窗口轮询会继续兜底。
            McdevProbe.LOGGER.warn("Mcdev 创建时隐藏主窗口失败：{}", t.getMessage());
        }
    }
}
