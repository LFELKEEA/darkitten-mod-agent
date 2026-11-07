package net.fabricmc.loom.nativeplatform;

import java.nio.file.Path;
import java.util.Collections;
import java.util.List;

/** 本地库不可用时的空实现：NeoFormRuntime 只在排查文件锁时调用它。 */
public final class LoomNativePlatform {
    private LoomNativePlatform() {
    }

    public static boolean isSupported() {
        return false;
    }

    public static List<ProcessHandle> getProcessesWithLockOn(Path path) {
        return Collections.emptyList();
    }

    public static List<String> getWindowTitlesForPid(long pid) {
        return Collections.emptyList();
    }
}
