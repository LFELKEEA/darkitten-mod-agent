from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dma import studio_bridge


def main() -> int:
    spec = studio_bridge.plan(
        "深海遗迹、发光珊瑚矿、潜水装备、潮汐能机器、可驯服海龙",
        "Forge",
        "1.20.1",
        scope="small",
    )
    print("PLAN", spec["identity"]["modid"], len(spec["content"]))
    print(studio_bridge.spec_markdown(spec)[:300])

    root = Path(tempfile.mkdtemp(prefix="dma-studio-"))
    print("DEST", root)
    result = studio_bridge.generate(
        idea="深海遗迹、发光珊瑚矿、潜水装备、潮汐能机器、可驯服海龙",
        loader="Forge",
        mc="1.20.1",
        dest_root=root,
        scope="small",
        force=True,
        use_mdk=False,
        audit_after=True,
        progress=lambda line: print("LOG", line),
    )
    print(studio_bridge.summarize(result))
    project = Path(result["project"])
    print("SPEC", (project / "mod-spec.json").is_file())
    print("GDD", (project / "GDD.md").is_file())
    print("MAIN", any(project.glob("src/main/java/**/*.java")))
    print("META", (project / ".dma/project.json").is_file())
    print("AUDIT", (result.get("audit") or {}).get("score"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
