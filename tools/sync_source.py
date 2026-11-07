from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "LFELKEEA/darkitten-mod-agent"
BRANCH = "main"
MESSAGE = "feat: 恢复多加载器并接入真实开发端验证（1.4.1）"

files = [
    "README.md",
    "build.py",
    "main.py",
    "requirements.txt",
    "requirements-build.txt",
    "tools/make_icon.py",
    "tools/sync_source.py",
]
files += sorted(
    path.relative_to(ROOT).as_posix()
    for path in (ROOT / "assets").rglob("*")
    if path.is_file()
)
files += sorted(
    path.relative_to(ROOT).as_posix() for path in (ROOT / "dma").rglob("*.py")
)
files += sorted(
    path.relative_to(ROOT).as_posix()
    for path in (ROOT / "dma" / "resources").rglob("*")
    if path.is_file()
    and not any(part in {"__pycache__", "build", ".gradle"} for part in path.parts)
    and path.suffix.lower() not in {".pyc", ".pyo", ".log"}
)
files += sorted(
    path.relative_to(ROOT).as_posix() for path in (ROOT / "tests").rglob("*.py")
)
files = list(dict.fromkeys(files))

EXECUTABLE = {"assets/wrapper/gradlew", "dma/resources/forge-1.20.1/gradlew", "dma/resources/fabric-1.20.1/gradlew", "dma/resources/neoforge-1.20.1/gradlew", "dma/resources/mcdev-probe/gradlew"}


def api(method: str, endpoint: str, payload: dict | None = None) -> dict:
    cmd = ["gh", "api", "--method", method, endpoint]
    data = None
    if payload is not None:
        cmd += ["--input", "-"]
        data = json.dumps(payload).encode("utf-8")
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"{method} {endpoint} 失败：{proc.stderr.decode('utf-8', 'replace')}")
    out = proc.stdout.decode("utf-8", "replace").strip()
    return json.loads(out) if out else {}


ref = api("GET", f"repos/{REPO}/git/ref/heads/{BRANCH}")
head = ref["object"]["sha"]
commit = api("GET", f"repos/{REPO}/git/commits/{head}")
base_override = os.environ.get("DMA_SYNC_BASE_TREE", "").strip()
if base_override:
    base_commit = api("GET", f"repos/{REPO}/git/commits/{base_override}")
    base_tree = base_commit["tree"]["sha"]
else:
    base_tree = commit["tree"]["sha"]

entries = []
for rel in files:
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"缺少文件：{rel}")
    blob = api(
        "POST",
        f"repos/{REPO}/git/blobs",
        {"content": base64.b64encode(path.read_bytes()).decode("ascii"), "encoding": "base64"},
    )
    mode = "100755" if rel in EXECUTABLE else "100644"
    entries.append({"path": rel, "mode": mode, "type": "blob", "sha": blob["sha"]})
    print("blob", rel, blob["sha"][:8], mode)

tree = api("POST", f"repos/{REPO}/git/trees", {"base_tree": base_tree, "tree": entries})
new_commit = api(
    "POST",
    f"repos/{REPO}/git/commits",
    {"message": MESSAGE, "tree": tree["sha"], "parents": [head]},
)
api("PATCH", f"repos/{REPO}/git/refs/heads/{BRANCH}", {"sha": new_commit["sha"], "force": False})
print("commit", new_commit["sha"])
print("url", new_commit["html_url"])
