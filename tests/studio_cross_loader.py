from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dma import studio_bridge


def main() -> int:
    targets = [
        ("NeoForge", "1.21.1"),
        ("Fabric", "26.3"),
        ("Fabric", "1.21.1"),
    ]
    root = Path(tempfile.mkdtemp(prefix="dma-studio-cross-"))
    print("DEST", root)
    for loader, mc in targets:
        print("===", loader, mc)
        result = studio_bridge.generate(
            idea="潮汐能机器、发光珊瑚矿、可驯服海龙、深海遗迹探索",
            loader=loader,
            mc=mc,
            dest_root=root,
            scope="small",
            force=True,
            use_mdk=False,
            audit_after=True,
            progress=lambda line: print("  LOG", line),
        )
        report = result.get("audit") or {}
        print("  PROJECT", result["project"])
        print("  AUDIT", report.get("ok"), report.get("score"), report.get("errors"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
