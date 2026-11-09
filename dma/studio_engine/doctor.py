"""Environment diagnostics."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .util import McmodError, find_java_homes, http_get, read_json, write_json
from .versions import FORGE_META, MOJANG_MANIFEST, NEOFORGE_META_MIRROR, FABRIC_API_META

ENDPOINTS = [
    ("Mojang", MOJANG_MANIFEST),
    ("Fabric API", FABRIC_API_META),
    ("Forge", FORGE_META),
    ("NeoForge (BMCLAPI mirror)", NEOFORGE_META_MIRROR),
]


def doctor(project: Path | None = None) -> dict[str, Any]:
    homes = find_java_homes()
    java = [{"path": str(j.path), "version": j.version, "major": j.major, "source": j.source} for j in homes]
    gradle_wrapper = None
    if project:
        for name in ("gradlew.bat", "gradlew"):
            p = project / name
            if p.exists():
                gradle_wrapper = str(p)
                break
    endpoints = []
    for name, url in ENDPOINTS:
        try:
            http_get(url, ttl=600, timeout=10)
            endpoints.append({"name": name, "ok": True, "url": url})
        except McmodError as exc:
            endpoints.append({"name": name, "ok": False, "url": url, "error": str(exc)[:200]})
    usage = shutil.disk_usage(str(project or Path.home()))
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "java_homes": java,
        "default_java_home": os.environ.get("JAVA_HOME", ""),
        "gradle_wrapper": gradle_wrapper,
        "gradle_on_path": shutil.which("gradle"),
        "endpoints": endpoints,
        "disk_free_gb": round(usage.free / (1024 ** 3), 1),
        "recommendations": [],
    }
    if not java:
        report["recommendations"].append("没有发现 JDK。安装 JDK 21 与 JDK 25，或设置 JAVA_HOME。")
    else:
        majors = {j["major"] for j in java}
        if 21 not in majors:
            report["recommendations"].append("缺少 JDK 21：1.20.5–1.21.x 需要它。")
        if 25 not in majors:
            report["recommendations"].append("缺少 JDK 25：MC 26.x 的官方 version JSON 要求 Java 25。")
    if usage.free < 8 * 1024 ** 3:
        report["recommendations"].append("磁盘剩余不足 8 GB：Gradle 依赖与 sources 会占用较大空间。")
    for ep in endpoints:
        if not ep["ok"]:
            report["recommendations"].append(f"{ep['name']} 元数据不可达；将使用缓存/镜像/离线种子。")
    if project:
        write_json(project / ".mcmod" / "doctor.json", report)
    return report


def summarize(report: dict[str, Any]) -> str:
    lines = [
        f"Python: {report['python'].split()[0]}",
        f"平台: {report['platform']}",
        f"磁盘剩余: {report['disk_free_gb']} GB",
        "JDK:",
    ]
    for j in report["java_homes"]:
        lines.append(f"  - {j['major']} ({j['version']}) {j['path']}")
    if not report["java_homes"]:
        lines.append("  - 未发现")
    lines.append("元数据端点:")
    for ep in report["endpoints"]:
        lines.append(f"  - {'OK' if ep['ok'] else 'FAIL'} {ep['name']}")
    if report.get("recommendations"):
        lines.append("建议:")
        lines += [f"  - {r}" for r in report["recommendations"]]
    return "\n".join(lines)
