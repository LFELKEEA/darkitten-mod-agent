"""Structured art review for textures and screenshots (P1-09 / TOOL-15).

Two layers are provided:

* deterministic pixel metrics that can run offline and gate a build;
* a prompt builder so a vision model can produce the qualitative review that
  metrics cannot (readability, style match, composition).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class VisionReview:
    path: str = ""
    score: int = 0
    ok: bool = True
    style: str = "item"
    metrics: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": self.score,
            "ok": self.ok,
            "style": self.style,
            "metrics": self.metrics,
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
        }

    def markdown(self) -> str:
        lines = [
            f"## 美术评审：`{Path(self.path).name}`",
            "",
            f"- 评分：{self.score}/100（{'通过' if self.ok else '需修改'}）",
            f"- 类型：{self.style}",
        ]
        if self.metrics:
            items = " / ".join(f"{key}={value}" for key, value in self.metrics.items())
            lines.append(f"- 指标：{items}")
        if self.issues:
            lines.extend(["", "### 问题", ""])
            lines.extend(f"- {item}" for item in self.issues)
        if self.suggestions:
            lines.extend(["", "### 建议", ""])
            lines.extend(f"- {item}" for item in self.suggestions)
        return "\n".join(lines) + "\n"


def _pil():
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("需要 Pillow 才能进行美术评审") from exc
    return Image


def analyze(path: Path) -> dict:
    Image = _pil()
    target = Path(path)
    image = Image.open(target).convert("RGBA")
    width, height = image.size
    pixels = list(image.getdata())
    total = max(1, len(pixels))
    alpha = [item[3] for item in pixels]
    transparent = sum(1 for value in alpha if value < 255)
    fully_transparent = sum(1 for value in alpha if value == 0)
    colors = len({item for item in pixels})
    gray = image.convert("L")
    gray_pixels = list(gray.getdata())
    mean = sum(gray_pixels) / total
    variance = sum((value - mean) ** 2 for value in gray_pixels) / total
    edges = 0
    for y in range(height):
        for x in range(width):
            index = y * width + x
            value = gray_pixels[index]
            if x + 1 < width:
                edges += abs(value - gray_pixels[index + 1])
            if y + 1 < height:
                edges += abs(value - gray_pixels[index + width])
    edge_density = edges / max(1, total * 2 * 255)
    mirror = 0
    for y in range(height):
        for x in range(width // 2):
            left = gray_pixels[y * width + x]
            right = gray_pixels[y * width + (width - 1 - x)]
            mirror += abs(left - right)
    symmetry = 1 - mirror / max(1, (height * (width // 2)) * 255)
    return {
        "size": [width, height],
        "unique_colors": colors,
        "alpha_coverage": round(1 - fully_transparent / total, 4),
        "translucent_ratio": round(transparent / total, 4),
        "brightness": round(mean, 2),
        "contrast": round(variance ** 0.5, 2),
        "edge_density": round(edge_density, 4),
        "symmetry": round(symmetry, 4),
    }


def review(
    path: Path,
    *,
    style: str = "item",
    expected_size: tuple[int, int] | None = None,
    max_colors: int | None = None,
) -> VisionReview:
    target = Path(path)
    result = VisionReview(path=str(target), style=style)
    if not target.is_file():
        result.ok = False
        result.issues = ["文件不存在"]
        return result
    metrics = analyze(target)
    result.metrics = metrics
    width, height = metrics["size"]
    colors = metrics["unique_colors"]
    issues = result.issues
    suggestions = result.suggestions

    if style == "screenshot":
        if width < 320 or height < 240:
            issues.append(f"截图尺寸过小：{width}x{height}")
        if metrics["unique_colors"] < 16:
            issues.append("截图几乎是纯色，可能是黑屏/未加载")
        if metrics["edge_density"] < 0.002:
            issues.append("画面几乎没有边缘细节，可能是空场景")
        if metrics["contrast"] < 5:
            issues.append("对比度过低，UI 可能不可读")
        if width < 1280:
            suggestions.append("用 1920x1080 或窗口原始尺寸截图，便于检查 UI 溢出。")
    elif style == "block":
        if (width, height) not in {(16, 16), (32, 32), (64, 64)}:
            issues.append(f"方块贴图建议 16/32/64，当前 {width}x{height}")
        if metrics["alpha_coverage"] < 0.999:
            suggestions.append("方块贴图通常应为不透明；若确需透明请确认渲染类型。")
        limit = max_colors or 64
        if colors > limit:
            issues.append(f"颜色数 {colors} 超过像素风上限 {limit}")
    else:
        if metrics["alpha_coverage"] > 0.999:
            suggestions.append("物品贴图一般应有透明背景，避免方块状轮廓。")
        if (width, height) not in {(16, 16), (32, 32)}:
            issues.append(f"物品贴图建议 16x16/32x32，当前 {width}x{height}")
    if expected_size and tuple(metrics["size"]) != tuple(expected_size):
        issues.append(f"尺寸与声明 {expected_size} 不一致")
    if metrics["edge_density"] < 0.01 and style != "screenshot":
        suggestions.append("细节偏少，可增加明暗对比或少量高光。")
    if metrics["symmetry"] < 0.55 and style == "block":
        suggestions.append("方块贴图可读性依赖可平铺结构，检查接缝是否明显。")

    penalty = len(issues) * 18 + len(suggestions) * 4
    result.score = max(0, 100 - penalty)
    result.ok = not issues
    return result


def review_batch(paths: Iterable[Path], *, style: str = "item") -> list[VisionReview]:
    return [review(path, style=style) for path in paths]


def compare(left: Path, right: Path, *, out: Path | None = None) -> dict:
    Image = _pil()
    from PIL import ImageChops, ImageStat
    a = Image.open(left).convert("RGB")
    b = Image.open(right).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b)
    bbox = diff.getbbox()
    mean = ImageStat.Stat(diff).mean
    ratio = (sum(mean) / 3) / 255
    if out:
        diff.save(out)
    return {
        "difference": round(ratio, 6),
        "similar": ratio < 0.02,
        "bbox": list(bbox) if bbox else None,
        "size": list(a.size),
        "diff_path": str(out) if out else "",
    }


def build_review_prompt(context: str, images: Iterable[str] = ()) -> str:
    paths = "\n".join(f"- {item}" for item in images) or "- （按对话中提供的图片）"
    return (
        "你是一名 Minecraft 美术评审。请只输出结构化 JSON，字段为：\n"
        '{"score": 0-100, "issues": ["..."], "suggestions": ["..."], "style_match": "..."}\n'
        "评审维度：像素风一致性、轮廓可读性、明暗对比、调色板数量、与描述的风格匹配、"
        "UI 是否溢出/遮挡、截图中的资源是否加载成功。\n\n"
        f"项目上下文：{context}\n\n图片：\n{paths}\n"
    )


def parse_review(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {"score": 0, "issues": ["评审输出不是 JSON"], "suggestions": []}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {"score": 0, "issues": ["评审 JSON 解析失败"], "suggestions": []}
    if not isinstance(data, dict):
        return {"score": 0, "issues": ["评审结果不是对象"], "suggestions": []}
    return {
        "score": int(data.get("score") or 0),
        "issues": [str(item) for item in data.get("issues") or []],
        "suggestions": [str(item) for item in data.get("suggestions") or []],
        "style_match": str(data.get("style_match") or ""),
    }
