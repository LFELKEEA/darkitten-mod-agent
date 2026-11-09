from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dma import studio_bridge


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    report = studio_bridge.audit_project(root, progress=lambda line: print("LOG", line))
    print("AUDIT_OK", report.get("ok"), report.get("score"))
    report = studio_bridge.build_project(
        root, timeout=1800, progress=lambda line: print("BUILD", line)
    )
    print("BUILD_OK", report.get("ok"), "iterations", report.get("iterations"))
    for error in report.get("errors", [])[:10]:
        print("ERR", error)
    jar = studio_bridge.primary_jar(root)
    print("JAR", jar)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
