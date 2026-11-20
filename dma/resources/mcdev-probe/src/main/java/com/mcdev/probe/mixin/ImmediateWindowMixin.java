package com.mcdev.probe.mixin;

import com.mcdev.probe.StartupWindow;
import org.lwjgl.glfw.GLFW;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * 在 Forge 真正创建主窗口之前改写 GLFW 的可见性提示。
 *
 * Window 构造器已经把窗口尺寸等 hint 设好，随后调用
 * ImmediateWindowHandler.setupMinecraftWindow(...) 创建句柄。这里在静态方法
 * 入口追加 GLFW_VISIBLE=false，窗口从 glfwCreateWindow 那一刻起就不映射到
 * 桌面，避免 Window 构造器 RETURN 之前那几十毫秒的闪现。
 *
 * 只在客户端生效（mixins.json 的 client 段）。
 */
@Mixin(net.minecraftforge.fml.loading.ImmediateWindowHandler.class)
public abstract class ImmediateWindowMixin {

    @Inject(method = "setupMinecraftWindow", at = @At("HEAD"), remap = false)
    private static void mcdev$hideBeforeCreate(CallbackInfo ci) {
        if (StartupWindow.backgroundRequested()) {
            GLFW.glfwWindowHint(GLFW.GLFW_VISIBLE, GLFW.GLFW_FALSE);
        }
    }
}
