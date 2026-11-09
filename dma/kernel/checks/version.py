"""Version / migration / dependency checks (P3-28, P3-30, P3-35, P3-36)."""

from __future__ import annotations

import re
from typing import Iterable

from .common import ProjectContext, finding

_VERSION_BRANCH = re.compile(
    r"\b(mc_version|MC_VERSION|is1[._]?\d+|is_1_\d+|>=?\s*1\.2\d|ForgeVersion|NeoForgeVersion)\b"
)

# Offline advisory table: (group, artifact, fixed_version, severity, note).
ADVISORIES: list[tuple[str, str, str, str, str]] = [
    ("org.apache.logging.log4j", "log4j-core", "2.17.1", "error",
     "CVE-2021-44228/45046（Log4Shell）"),
    ("org.apache.logging.log4j", "log4j-api", "2.17.1", "warning",
     "与 log4j-core 同批修复"),
    ("org.yaml", "snakeyaml", "2.0", "error",
     "CVE-2022-1471 反序列化"),
    ("com.google.code.gson", "gson", "2.8.9", "warning",
     "CVE-2022-25647"),
    ("org.apache.commons", "commons-text", "1.10.0", "error",
     "CVE-2022-42889 Text4Shell"),
    ("io.netty", "netty-handler", "4.1.68.Final", "warning",
     "多项 Netty 解码器问题"),
    ("org.bouncycastle", "bcprov-jdk15on", "1.68", "warning",
     "CVE-2020-15522 等"),
]


def run(ctx: ProjectContext) -> Iterable[Finding]:
    yield from _adapter_isolation(ctx)
    yield from _migration(ctx)
    yield from _git_version(ctx)
    yield from _vulnerabilities(ctx)


# ----------------------------------------------------------------------
def _adapter_isolation(ctx: ProjectContext) -> Iterable[Finding]:
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        if any(part in rel.lower() for part in ("/compat/", "/adapter/", "/platform/", "/version/")):
            continue
        text = ctx.read(java.path)
        match = _VERSION_BRANCH.search(text)
        if not match:
            continue
        yield finding(
            "P3-28",
            f"{java_class.name} 在业务代码里直接判断 MC/loader 版本（{match.group(1)}）",
            path=rel,
            line=text[: match.start()].count("\n") + 1,
            severity="info",
            suggestion="把版本差异抽到 adapter/compat 层，业务代码只调用统一接口。",
        )


def _migration(ctx: ProjectContext) -> Iterable[Finding]:
    modid = _modid(ctx)
    if not modid:
        return
    folder = f"src/main/resources/data/{modid}/dma/migration"
    java_text = "\n".join(ctx.read(java.path) for java, _ in ctx.classes())
    has_handler = bool(re.search(r"\b(DataFixer|Schema\w*|Migration\w*|migrate\w*)\b", java_text))
    if ctx.exists(folder) and not has_handler:
        yield finding(
            "P3-30",
            f"存在迁移表 `{folder}`，但代码里没有读取/执行迁移的入口",
            path=folder,
            severity="warning",
            suggestion="添加迁移加载器：读取旧版本标记 → 备份 → 按表重写 ID/配置键 → 记录结果。",
        )
    serializes = bool(re.search(r"\b(CompoundTag|Codec|SavedData|AttachmentType)\b", java_text))
    if serializes and not ctx.exists(folder) and not has_handler:
        yield finding(
            "P3-30",
            "项目包含持久化数据，但没有迁移目录或迁移入口",
            path="src/main/java",
            severity="info",
            suggestion="在 dma/migration 下登记版本迁移，并保证旧存档可回退。",
        )


def _git_version(ctx: ProjectContext) -> Iterable[Finding]:
    props = ctx.read_rel("gradle.properties")
    build = ctx.read_rel("build.gradle") + "\n" + ctx.read_rel("build.gradle.kts")
    if re.search(r"(git describe|gitVersion|version\.properties|dmaVersion)", props + build):
        return
    match = re.search(r"^\s*mod_version\s*=\s*(\S+)", props, re.MULTILINE)
    if not match:
        return
    yield finding(
        "P3-35",
        f"版本号 `{match.group(1)}` 是手写常量，未与 Git tag/commit 绑定",
        path="gradle.properties",
        severity="info",
        suggestion="用 `git describe --tags --always --dirty` 生成版本，并在 CI 中校验 tag 与发布号一致。",
    )


def _vulnerabilities(ctx: ProjectContext) -> Iterable[Finding]:
    build = ctx.read_rel("build.gradle") + "\n" + ctx.read_rel("build.gradle.kts")
    if not build.strip():
        return
    deps = _dependencies(build)
    for group, artifact, fixed, severity, note in ADVISORIES:
        for dep_group, dep_artifact, version in deps:
            if dep_group != group or dep_artifact != artifact:
                continue
            if _older(version, fixed):
                yield finding(
                    "P3-36",
                    f"{group}:{artifact}:{version} 低于安全版本 {fixed}（{note}）",
                    path="build.gradle",
                    severity=severity,
                    suggestion=f"升级到 {fixed} 或更高，并跑一遍回归测试。",
                )


# ----------------------------------------------------------------------
def _dependencies(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    pattern = re.compile(r"['\"]([\w.\-]+):([\w.\-]+):([\w.\-+]+)['\"]")
    for match in pattern.finditer(text):
        group, artifact, version = match.groups()
        if version.startswith("$"):
            continue
        rows.append((group, artifact, version))
    return rows


def _older(version: str, fixed: str) -> bool:
    left = _numbers(version)
    right = _numbers(fixed)
    if not left or not right:
        return False
    return left < right


def _numbers(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(item) for item in parts[:4])


def _modid(ctx: ProjectContext) -> str:
    text = ctx.read_rel("gradle.properties")
    match = re.search(r"^\s*mod_id\s*=\s*([\w.\-]+)", text, re.MULTILINE)
    if match:
        return match.group(1)
    match = re.search(r'"modid"\s*:\s*"([\w.\-]+)"', ctx.read_rel("src/main/resources/fabric.mod.json"))
    return match.group(1) if match else ""
