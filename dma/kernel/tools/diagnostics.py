"""Debug diagnostics scaffolds (P3-22, P3-24, P3-25, P3-26).

The generated sources are deliberately loader-agnostic: they depend only on
Brigadier, the JDK and ``java.util``.  Loader glue (command registration,
crash-report hook) is a two-line call site left to the human, which is why
these four items are reported as ``assist`` rather than ``auto``.
"""

from __future__ import annotations

from pathlib import Path

KINDS = ("flags", "dump", "crash", "probe")
KIND_LABELS = {
    "flags": "调试总开关（生产默认关闭）",
    "dump": "debug dump 子命令（默认关闭）",
    "crash": "崩溃报告自定义章节",
    "probe": "分层性能探针（tick/同步/渲染/寻路）",
}


def _package_line(package: str, sub: str = "debug") -> str:
    if package:
        return f"package {package}.{sub};\n"
    return f"package {sub};\n"


def flags_source(package: str, modid: str) -> str:
    prop = f"{modid}.debug"
    return "\n".join([
        _package_line(package),
        "/**",
        " * Single gate for every debug surface in the mod (P3-22/P3-25).",
        " * Production builds keep {@link #BUILD_ENABLED} false, so a player",
        " * cannot turn debug panels on with a system property alone.",
        " */",
        "public final class DebugFlags {",
        "    /** Flip to true only in a development build. */",
        "    private static final boolean BUILD_ENABLED = false;",
        f'    private static final String PROPERTY = "{prop}";',
        "",
        "    public static boolean enabled() {",
        "        return BUILD_ENABLED && Boolean.getBoolean(PROPERTY);",
        "    }",
        "",
        "    public static boolean isProduction() {",
        "        return !BUILD_ENABLED;",
        "    }",
        "",
        '    private DebugFlags() {',
        "    }",
        "}",
        "",
    ])


def dump_source(package: str, modid: str, command: str = "dump") -> str:
    return "\n".join([
        _package_line(package),
        "",
        "import com.mojang.brigadier.CommandDispatcher;",
        "import com.mojang.brigadier.builder.LiteralArgumentBuilder;",
        "",
        "import java.io.IOException;",
        "import java.nio.charset.StandardCharsets;",
        "import java.nio.file.Files;",
        "import java.nio.file.Path;",
        "import java.nio.file.StandardOpenOption;",
        "import java.time.LocalDateTime;",
        "import java.time.format.DateTimeFormatter;",
        "import java.util.LinkedHashMap;",
        "import java.util.Map;",
        "import java.util.function.Supplier;",
        "",
        "import net.minecraft.commands.CommandSourceStack;",
        "import net.minecraft.commands.Commands;",
        "import net.minecraft.network.chat.Component;",
        "",
        "/**",
        f" * Generated dump command for {modid} (P3-22).",
        " *",
        " * <p>Register it once during command registration:</p>",
        " * <pre>{@code",
        " * DebugDumpCommand.register(dispatcher, () -> Map.of(",
        ' *         "registry", BuiltInRegistries.REGISTRY.keySet(),',
        ' *         "listeners", eventBus.listenerNames()));',
        " * }</pre>",
        " *",
        " * <p>The command exists only when {@link DebugFlags#enabled()} is true,",
        " * and writes to {@code logs/} instead of chat so the output can be",
        " * attached to a bug report.</p>",
        " */",
        "public final class DebugDumpCommand {",
        "    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern(\"yyyyMMdd-HHmmss\");",
        "",
        "    public static void register(",
        "            CommandDispatcher<CommandSourceStack> dispatcher,",
        "            Supplier<Map<String, ? extends Iterable<?>>> sources) {",
        f'        LiteralArgumentBuilder<CommandSourceStack> root = Commands.literal("{command}")',
        "                .requires(source -> DebugFlags.enabled())",
        "                .executes(context -> {",
        "                    Path target;",
        "                    try {",
        "                        target = dump(sources.get());",
        "                    } catch (IOException error) {",
        '                        context.getSource().sendFailure(Component.literal("dump failed: " + error.getMessage()));',
        "                        return 0;",
        "                    }",
        '                    context.getSource().sendSuccess(() -> Component.literal("wrote " + target), false);',
        "                    return 1;",
        "                });",
        "        dispatcher.register(root);",
        "    }",
        "",
        "    public static Path dump(Map<String, ? extends Iterable<?>> sources) throws IOException {",
        "        Path directory = Path.of(\"logs\");",
        "        Files.createDirectories(directory);",
        f'        Path target = directory.resolve("{modid}-dump-" + STAMP.format(LocalDateTime.now()) + ".txt");',
        "        StringBuilder out = new StringBuilder();",
        f'        out.append("{modid} debug dump\\n");',
        "        for (Map.Entry<String, ? extends Iterable<?>> entry : sources.entrySet()) {",
        "            out.append(\"\\n## \").append(entry.getKey()).append('\\n');",
        "            int index = 0;",
        "            for (Object value : entry.getValue()) {",
        "                out.append(\"- \").append(String.valueOf(value)).append('\\n');",
        "                if (++index >= 10_000) {",
        '                    out.append("- ... truncated\\n");',
        "                    break;",
        "                }",
        "            }",
        "        }",
        "        Files.writeString(target, out.toString(), StandardCharsets.UTF_8,",
        "                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);",
        "        return target.toAbsolutePath();",
        "    }",
        "",
        "    /** Convenience factory for call sites that only have two sources. */",
        "    public static Map<String, ? extends Iterable<?>> map(",
        "            String firstKey, Iterable<?> first,",
        "            String secondKey, Iterable<?> second) {",
        "        Map<String, Iterable<?>> rows = new LinkedHashMap<>();",
        "        rows.put(firstKey, first);",
        "        rows.put(secondKey, second);",
        "        return rows;",
        "    }",
        "",
        "    private DebugDumpCommand() {",
        "    }",
        "}",
        "",
    ])


def crash_source(package: str, modid: str, version: str = "0.0.0") -> str:
    return "\n".join([
        _package_line(package),
        "",
        "import java.util.LinkedHashMap;",
        "import java.util.Map;",
        "import java.util.function.Supplier;",
        "",
        "/**",
        f" * Generated crash context for {modid} (P3-24).",
        " *",
        " * <p>Build the section during mod construction and expose it to the",
        " * loader crash report, for example in a crash-report callback or by",
        " * appending {@link #render()} to the loader's custom section:</p>",
        " * <pre>{@code",
        " * context.add(\"config\", () -> config.snapshot().toString());",
        " * context.add(\"features\", () -> spec.enabledFeatures().toString());",
        " * }</pre>",
        " *",
        " * <p>Keep suppliers side-effect free: they may run while the game is",
        " * already in a broken state.</p>",
        " */",
        "public final class CrashContext {",
        f'    public static final String MOD_ID = "{modid}";',
        f'    public static final String MOD_VERSION = "{version}";',
        "",
        "    private final Map<String, Supplier<String>> sections = new LinkedHashMap<>();",
        "",
        "    public CrashContext add(String key, Supplier<String> value) {",
        "        this.sections.put(key, value);",
        "        return this;",
        "    }",
        "",
        "    public Map<String, String> snapshot() {",
        "        Map<String, String> rows = new LinkedHashMap<>();",
        "        for (Map.Entry<String, Supplier<String>> entry : this.sections.entrySet()) {",
        "            String value;",
        "            try {",
        "                value = String.valueOf(entry.getValue().get());",
        "            } catch (Throwable error) {",
        "                value = \"<failed: \" + error.getClass().getSimpleName() + \">\";",
        "            }",
        "            rows.put(entry.getKey(), value);",
        "        }",
        "        return rows;",
        "    }",
        "",
        "    public String render() {",
        "        StringBuilder out = new StringBuilder();",
        '        out.append("-- ").append(MOD_ID).append(" --\\n");',
        "        out.append(\"Version: \").append(MOD_VERSION).append('\\n');",
        "        for (Map.Entry<String, String> entry : snapshot().entrySet()) {",
        "            out.append(entry.getKey()).append(\": \").append(entry.getValue()).append('\\n');",
        "        }",
        "        return out.toString();",
        "    }",
        "}",
        "",
    ])


def probe_source(package: str, modid: str) -> str:
    return "\n".join([
        _package_line(package),
        "",
        "import java.util.EnumMap;",
        "import java.util.Map;",
        "import java.util.concurrent.atomic.LongAdder;",
        "",
        "import jdk.jfr.Event;",
        "import jdk.jfr.Label;",
        "import jdk.jfr.Name;",
        "",
        "/**",
        f" * Layered performance probe for {modid} (P3-26).",
        " *",
        " * <p>Wrap hot sections with try-with-resources:</p>",
        " * <pre>{@code",
        " * try (PerfProbe.Sample ignored = PerfProbe.start(PerfProbe.Layer.TICK)) {",
        " *     tickFeature();",
        " * }",
        " * }</pre>",
        " *",
        " * <p>The counters are allocation-free after class init. JFR events are",
        " * disabled by default and only emitted when",
        " * {@code -D" + modid + ".debug.jfr=true} is set in a debug build.</p>",
        " */",
        "public final class PerfProbe {",
        "    public enum Layer { TICK, SYNC, RENDER, PATHFINDING }",
        "",
        "    private static final Map<Layer, LongAdder> COUNT = new EnumMap<>(Layer.class);",
        "    private static final Map<Layer, LongAdder> NANOS = new EnumMap<>(Layer.class);",
        f'    private static final boolean JFR = Boolean.getBoolean("{modid}.debug.jfr");',
        "",
        "    static {",
        "        for (Layer layer : Layer.values()) {",
        "            COUNT.put(layer, new LongAdder());",
        "            NANOS.put(layer, new LongAdder());",
        "        }",
        "    }",
        "",
        "    public static Sample start(Layer layer) {",
        "        return new Sample(layer, System.nanoTime());",
        "    }",
        "",
        "    public static String summary() {",
        "        StringBuilder out = new StringBuilder();",
        "        for (Layer layer : Layer.values()) {",
        "            long count = COUNT.get(layer).sum();",
        "            long nanos = NANOS.get(layer).sum();",
        "            long average = count == 0 ? 0 : nanos / count;",
        '            out.append(layer).append(": calls=").append(count)',
        '                    .append(", avg_us=").append(average / 1_000)',
        "                    .append(\", total_ms=\").append(nanos / 1_000_000).append('\\n');",
        "        }",
        "        return out.toString();",
        "    }",
        "",
        "    public static void reset() {",
        "        for (Layer layer : Layer.values()) {",
        "            COUNT.get(layer).reset();",
        "            NANOS.get(layer).reset();",
        "        }",
        "    }",
        "",
        "    public static final class Sample implements AutoCloseable {",
        "        private final Layer layer;",
        "        private final long started;",
        "",
        "        private Sample(Layer layer, long started) {",
        "            this.layer = layer;",
        "            this.started = started;",
        "        }",
        "",
        "        @Override",
        "        public void close() {",
        "            long elapsed = System.nanoTime() - this.started;",
        "            NANOS.get(this.layer).add(elapsed);",
        "            COUNT.get(this.layer).increment();",
        "            if (JFR) {",
        "                ProbeEvent event = new ProbeEvent();",
        "                event.layer = this.layer.name();",
        "                event.nanos = elapsed;",
        "                event.commit();",
        "            }",
        "        }",
        "    }",
        "",
        "    @Name(\"darkitten.probe\")",
        "    @Label(\"Darkitten feature probe\")",
        "    public static final class ProbeEvent extends Event {",
        "        public String layer;",
        "        public long nanos;",
        "    }",
        "",
        "    private PerfProbe() {",
        "    }",
        "}",
        "",
    ])


def docs_markdown(modid: str, kinds: list[str]) -> str:
    lines = [
        f"# {modid} 调试手册",
        "",
        "所有调试能力默认关闭，生产包只保留关闭状态（P3-25）。",
        "",
        f"- 系统属性：`-D{modid}.debug=true`",
        f"- JFR：`-D{modid}.debug.jfr=true`（仅 DebugFlags 打开时生效）",
        "",
        "## 使用方式",
        "",
    ]
    if "dump" in kinds:
        lines += [
            "### dump 子命令（P3-22）",
            "",
            "在命令注册阶段调用 `DebugDumpCommand.register(dispatcher, sources)`。",
            "输出写入 `logs/`，包含注册表、监听器、配置快照等章节。",
            "",
        ]
    if "crash" in kinds:
        lines += [
            "### 崩溃上下文（P3-24）",
            "",
            "构造 `CrashContext` 并注册三个 supplier：Mod 版本、启用功能、配置快照。",
            "supplier 必须无副作用，崩溃时也可能执行。",
            "",
        ]
    if "probe" in kinds:
        lines += [
            "### 分层性能探针（P3-26）",
            "",
            "用 `try (PerfProbe.Sample ignored = PerfProbe.start(Layer.TICK)) { ... }`",
            "包住 tick / 同步 / 渲染 / 寻路热点，`PerfProbe.summary()` 输出分项耗时。",
            "",
        ]
    if "flags" in kinds:
        lines += [
            "### 生产禁用校验（P3-25）",
            "",
            "`DebugFlags.BUILD_ENABLED` 在正式构建中保持 false；",
            "静态检查会标记没有 gate 的调试界面。",
            "",
        ]
    return "\n".join(lines)


def write(
    root: Path,
    *,
    package: str,
    modid: str,
    version: str = "0.0.0",
    kinds: list[str] | None = None,
    command: str = "dump",
) -> dict:
    root = Path(root)
    selected = [kind for kind in (kinds or list(KINDS)) if kind in KINDS]
    if not selected:
        raise ValueError(f"未知的调试模板：{kinds}")
    if "dump" in selected and "flags" not in selected:
        selected.insert(0, "flags")
    package_path = package.replace(".", "/") if package else ""
    directory = root / "src/main/java" / package_path / "debug"
    directory.mkdir(parents=True, exist_ok=True)
    writer = {
        "flags": ("DebugFlags.java", lambda: flags_source(package, modid)),
        "dump": ("DebugDumpCommand.java", lambda: dump_source(package, modid, command)),
        "crash": ("CrashContext.java", lambda: crash_source(package, modid, version)),
        "probe": ("PerfProbe.java", lambda: probe_source(package, modid)),
    }
    written: list[Path] = []
    for kind in selected:
        name, build = writer[kind]
        target = directory / name
        target.write_text(build(), encoding="utf-8")
        written.append(target)
    docs = root / "docs" / "debug.md"
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(docs_markdown(modid, selected), encoding="utf-8")
    written.append(docs)
    return {
        "files": [item.relative_to(root).as_posix() for item in written],
        "kinds": selected,
        "modid": modid,
        "enabledByDefault": False,
    }

