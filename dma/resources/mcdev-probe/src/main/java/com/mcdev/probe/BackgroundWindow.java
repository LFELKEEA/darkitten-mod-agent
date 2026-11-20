package com.mcdev.probe;

import net.minecraft.client.Minecraft;
import org.lwjgl.glfw.GLFW;

import java.io.File;
import java.io.FileInputStream;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

/**
 * 后台运行：让开发客户端继续渲染、继续响应探针，但不在桌面上显示任何窗口。
 *
 * 配置读取顺序：
 * 1. JVM 参数 -Dmcdev.probe.background=true/false
 * 2. 游戏目录 config/mcdev-probe.properties 里的 background=true/false
 *
 * 只要启用了后台模式，同时关闭 pauseOnLostFocus，避免隐藏窗口后单机存档
 * 自动打开暂停菜单、把玩家输入全部吞掉。通过 GLFW 隐藏窗口而不是最小化，
 * 这样渲染线程仍然工作，截图接口拿到的也是真实画面。
 */
public final class BackgroundWindow {
    private static final String CONFIG_NAME = "mcdev-probe.properties";
    private static Boolean hidden = null;

    private BackgroundWindow() {
    }

    private static Properties config(Minecraft mc) {
        Properties props = new Properties();
        File file = new File(mc.gameDirectory, "config/" + CONFIG_NAME);
        if (file.isFile()) {
            try (FileInputStream in = new FileInputStream(file)) {
                props.load(in);
            } catch (Exception e) {
                McdevProbe.LOGGER.warn("读取 {} 失败：{}", file, e.getMessage());
            }
        }
        return props;
    }

    private static String setting(Minecraft mc, String key, String fallback) {
        String override = System.getProperty("mcdev.probe." + key);
        if (override != null && !override.isBlank()) {
            return override.trim();
        }
        String value = config(mc).getProperty(key);
        return value == null || value.isBlank() ? fallback : value.trim();
    }

    private static boolean flag(Minecraft mc, String key, boolean fallback) {
        return Boolean.parseBoolean(setting(mc, key, Boolean.toString(fallback)));
    }

    public static boolean requested(Minecraft mc) {
        return flag(mc, "background", false);
    }

    /** 客户端初始化完成后调用；返回是否成功隐藏。 */
    public static synchronized Map<String, Object> apply(Minecraft mc) {
        Map<String, Object> data = status(mc);
        boolean wanted = requested(mc);
        data.put("requested", wanted);
        if (!wanted) {
            hidden = false;
            data.put("applied", false);
            return data;
        }
        try {
            mc.options.pauseOnLostFocus = false;
            long handle = mc.getWindow().getWindow();
            GLFW.glfwSetWindowAttrib(handle, GLFW.GLFW_FOCUS_ON_SHOW, GLFW.GLFW_FALSE);
            GLFW.glfwHideWindow(handle);
            // 隐藏窗口后没人看帧率，关掉垂直同步减少无谓等待。
            GLFW.glfwSwapInterval(0);
            hidden = true;
            data.put("applied", true);
            data.put("visible", false);
            McdevProbe.LOGGER.info("Mcdev 客户端已转入后台：窗口已隐藏，截图与探针保持可用");
        } catch (Throwable t) {
            hidden = false;
            data.put("applied", false);
            data.put("error", String.valueOf(t.getMessage()));
            McdevProbe.LOGGER.warn("隐藏客户端窗口失败：{}", t.getMessage());
        }
        data.put("pauseOnLostFocus", mc.options.pauseOnLostFocus);
        return data;
    }

    public static synchronized Map<String, Object> setVisible(Minecraft mc, boolean visible, boolean focus) {
        Map<String, Object> data = new LinkedHashMap<>();
        try {
            long handle = mc.getWindow().getWindow();
            if (visible) {
                GLFW.glfwShowWindow(handle);
                if (focus) {
                    GLFW.glfwFocusWindow(handle);
                }
                mc.options.pauseOnLostFocus = true;
            } else {
                GLFW.glfwHideWindow(handle);
                mc.options.pauseOnLostFocus = false;
            }
            hidden = !visible;
            data.put("ok", true);
        } catch (Throwable t) {
            data.put("ok", false);
            data.put("error", String.valueOf(t.getMessage()));
        }
        data.putAll(status(mc));
        return data;
    }

    public static synchronized Map<String, Object> status(Minecraft mc) {
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("hidden", hidden == null ? null : hidden);
        data.put("requested", requested(mc));
        data.put("pauseOnLostFocus", mc.options.pauseOnLostFocus);
        try {
            long handle = mc.getWindow().getWindow();
            boolean glfwVisible = GLFW.glfwGetWindowAttrib(handle, GLFW.GLFW_VISIBLE) == GLFW.GLFW_TRUE;
            // GLFW 的属性不会感知 Win32 的 ShowWindow(SW_HIDE)，所以把
            // “本工具已经隐藏过”作为对外可见性的权威结果，同时保留原始属性。
            data.put("glfwVisible", glfwVisible);
            data.put("visible", hidden != null && hidden ? false : glfwVisible);
            data.put("focused", GLFW.glfwGetWindowAttrib(handle, GLFW.GLFW_FOCUSED) == GLFW.GLFW_TRUE);
            data.put("width", mc.getWindow().getWidth());
            data.put("height", mc.getWindow().getHeight());
        } catch (Throwable t) {
            data.put("visible", null);
            data.put("error", String.valueOf(t.getMessage()));
        }
        return data;
    }
}
