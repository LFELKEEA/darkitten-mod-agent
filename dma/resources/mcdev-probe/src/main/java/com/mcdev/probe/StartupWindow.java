package com.mcdev.probe;

import java.io.File;
import java.io.FileInputStream;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Properties;

/**
 * 窗口还没创建、Minecraft 实例也不存在时，读取后台模式开关。
 *
 * 只能看 JVM 参数和游戏目录里的探针配置；Gradle 的 run 配置通常把工作目录
 * 设成 run-client，但不同启动方式下 user.dir 可能停在工程根目录，所以同时
 * 尝试 run-client / run 子目录，再逐级向上找。
 */
public final class StartupWindow {

    private StartupWindow() {
    }

    public static boolean backgroundRequested() {
        String override = System.getProperty("mcdev.probe.background");
        if (override != null && !override.isBlank()) {
            return Boolean.parseBoolean(override.trim());
        }
        File base = new File(System.getProperty("user.dir", ".")).getAbsoluteFile();
        for (File dir : candidateGameDirs(base)) {
            File file = new File(dir, "config/mcdev-probe.properties");
            if (!file.isFile()) {
                continue;
            }
            Properties props = new Properties();
            try (FileInputStream in = new FileInputStream(file)) {
                props.load(in);
                return Boolean.parseBoolean(props.getProperty("background", "false").trim());
            } catch (Exception ignored) {
                return false;
            }
        }
        return false;
    }

    private static List<File> candidateGameDirs(File base) {
        LinkedHashSet<File> dirs = new LinkedHashSet<>();
        dirs.add(base);
        dirs.add(new File(base, "run-client"));
        dirs.add(new File(base, "run"));
        File parent = base.getParentFile();
        for (int i = 0; i < 3 && parent != null; i++, parent = parent.getParentFile()) {
            dirs.add(parent);
            dirs.add(new File(parent, "run-client"));
        }
        return new ArrayList<>(dirs);
    }
}
