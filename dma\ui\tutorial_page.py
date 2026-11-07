from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..version import APP_VERSION
from . import theme
from .widgets import card, tiny

C = theme.COLORS


class Shot(QWidget):
    """用 QPainter 画出的界面示意图，避免额外图片资源。"""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(330, 168)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor(C["line"]), 1))
        p.setBrush(QColor("#12151f"))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

        # 标题栏
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["bg_alt"]))
        p.drawRoundedRect(QRectF(1, 1, w - 2, 26), 9, 9)
        for i, color in enumerate(("#ef6b7a", "#e0b45c", "#4ec9a4")):
            p.setBrush(QColor(color))
            p.drawEllipse(QRectF(12 + i * 14, 9, 8, 8))
        p.setPen(QColor(C["muted"]))
        p.setFont(QFont("Microsoft YaHei UI", 7))
        p.drawText(QRectF(60, 4, 200, 18), Qt.AlignVCenter, "恶魔小猫工坊")

        body = QRectF(12, 38, w - 24, h - 50)
        handler = getattr(self, f"_draw_{self.kind}", None)
        if handler is not None:
            handler(p, body)
        p.end()

    # -- 通用小部件 -----------------------------------------------------
    def _text(self, p: QPainter, rect: QRectF, text: str, color: str = "text",
              size: int = 8, bold: bool = False) -> None:
        font = QFont("Microsoft YaHei UI", size)
        font.setBold(bold)
        p.setFont(font)
        p.setPen(QColor(C.get(color, color)))
        p.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)

    def _box(self, p: QPainter, rect: QRectF, fill: str = "card",
             line: str | None = "line", radius: int = 6) -> None:
        p.setPen(QPen(QColor(C[line]), 1) if line else Qt.NoPen)
        p.setBrush(QColor(C.get(fill, fill)))
        p.drawRoundedRect(rect, radius, radius)

    def _row(self, p: QPainter, y: float, label: str, value: str,
             accent: bool = False) -> None:
        self._text(p, QRectF(0, y, 70, 18), label, "muted", 8)
        self._box(p, QRectF(74, y + 1, 232, 16), "accent_soft" if accent else "card_soft")
        self._text(p, QRectF(82, y, 220, 18), value, "text" if accent else "muted", 8)

    # -- 各步骤示意 -----------------------------------------------------
    def _draw_config(self, p: QPainter, r: QRectF) -> None:
        self._row(p, r.top(), "服务通道", "OpenAI 兼容")
        self._row(p, r.top() + 26, "Base URL", "https://…/v1")
        self._row(p, r.top() + 52, "模型", "deepseek-v4.1-flash")
        self._row(p, r.top() + 78, "API Key", "sk-ws-••••••••", accent=True)
        self._box(p, QRectF(r.left(), r.bottom() - 20, 86, 20), "accent", None)
        self._text(p, QRectF(r.left() + 14, r.bottom() - 20, 70, 20), "测试连接", "#fff", 8, True)

    def _draw_project(self, p: QPainter, r: QRectF) -> None:
        self._row(p, r.top(), "Mod ID", "stardust_tools")
        self._row(p, r.top() + 26, "MC 版本", "1.21.1")
        self._row(p, r.top() + 52, "加载器", "NeoForge")
        p.setPen(QColor(C["ok"]))
        p.setBrush(QColor(C["ok"]))
        p.drawEllipse(QRectF(r.left() + 2, r.top() + 82, 8, 8))
        self._text(p, QRectF(r.left() + 16, r.top() + 77, 220, 18), "创建后立即拆解规格", "ok", 8)
        self._box(p, QRectF(r.right() - 100, r.bottom() - 22, 100, 22), "accent", None)
        self._text(p, QRectF(r.right() - 92, r.bottom() - 22, 88, 22), "创建并开始", "#fff", 8, True)

    def _draw_chat(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top(), 190, 34), "card_soft")
        self._text(p, QRectF(r.left() + 8, r.top() + 2, 175, 30),
                   "做一个会发光的矿石，挖掉回血", "text", 8)
        self._box(p, QRectF(r.right() - 205, r.top() + 42, 205, 44), "accent_soft")
        self._text(p, QRectF(r.right() - 197, r.top() + 44, 190, 18),
                   "规格已生成，等待确认", "text", 8, True)
        self._text(p, QRectF(r.right() - 197, r.top() + 62, 190, 18),
                   "确认后才开始写代码", "ok", 8)
        self._box(p, QRectF(r.left(), r.bottom() - 26, 52, 22), "accent", None)
        self._text(p, QRectF(r.left() + 8, r.bottom() - 26, 44, 22), "发送", "#fff", 8, True)
        self._text(p, QRectF(r.left() + 62, r.bottom() - 26, 120, 22), "Ctrl+Enter", "muted", 7)

    def _draw_pending(self, p: QPainter, r: QRectF) -> None:
        left = r.left()
        self._text(p, QRectF(left, r.top(), 160, 16), "待写入文件 / 已自动写入", "text", 8, True)
        files = [
            ("StardustOre.java", "自动写入"),
            ("StardustItem.java", "自动写入"),
            ("zh_cn.json", "生成"),
        ]
        for i, (name, state) in enumerate(files):
            y = r.top() + 24 + i * 25
            self._box(p, QRectF(left, y, 306, 20), "card_soft")
            p.setBrush(QColor(C["ok"] if state == "自动写入" else C["warn"]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(left + 7, y + 6, 8, 8))
            self._text(p, QRectF(left + 20, y, 180, 20), name, "text", 7)
            self._text(p, QRectF(left + 232, y, 70, 20), state, "ok" if state == "自动写入" else "warn", 7)

    def _draw_build(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top(), r.width(), r.height()), "bg", "line")
        lines = [
            ("> Task :compileJava", "muted"),
            ("BUILD SUCCESSFUL in 1m 12s", "ok"),
            ("6 actionable tasks: 6 executed", "muted"),
            ("> build/libs/stardust_tools-1.0.0.jar", "accent"),
        ]
        for i, (line, color) in enumerate(lines):
            self._text(p, QRectF(r.left() + 10, r.top() + 8 + i * 22, r.width() - 20, 20),
                       line, color, 8, color in {"ok", "accent"})

    def _draw_fix(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top() + 12, 142, 78), "card_soft")
        self._text(p, QRectF(r.left() + 8, r.top() + 18, 128, 18), "error:", "danger", 8, True)
        self._text(p, QRectF(r.left() + 8, r.top() + 38, 128, 18), "cannot find symbol", "danger", 7)
        self._text(p, QRectF(r.left() + 8, r.top() + 56, 128, 18), "L42 ModItems.java", "muted", 7)
        self._text(p, QRectF(r.left() + 148, r.top() + 34, 26, 30), "→", "accent", 15, True)
        self._box(p, QRectF(r.right() - 142, r.top() + 12, 142, 78), "card_soft")
        self._text(p, QRectF(r.right() - 134, r.top() + 18, 126, 18), "BUILD SUCCESSFUL", "ok", 8, True)
        self._text(p, QRectF(r.right() - 134, r.top() + 40, 126, 18), "自动修 1 轮", "muted", 7)
        self._text(p, QRectF(r.right() - 134, r.top() + 58, 126, 18), "已出包", "ok", 7)
        self._text(p, QRectF(r.left(), r.bottom() - 16, r.width(), 16),
                   "失败日志会直接回传给助手，不需要手贴", "muted", 7)

    def _draw_output(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top() + 10, 158, 100), "card_soft")
        self._text(p, QRectF(r.left() + 10, r.top() + 18, 138, 18), "build/libs", "muted", 8)
        self._box(p, QRectF(r.left() + 10, r.top() + 42, 138, 24), "accent_soft")
        self._text(p, QRectF(r.left() + 18, r.top() + 42, 130, 24),
                   "stardust_tools-1.0.0.jar", "text", 7, True)
        self._text(p, QRectF(r.left() + 10, r.top() + 74, 138, 18), "复制到 .minecraft/mods", "muted", 7)
        self._text(p, QRectF(r.left() + 174, r.top() + 18, 140, 18), "安装步骤", "text", 8, True)
        steps = ["1. 关闭游戏", "2. 放入 mods 文件夹", "3. 依赖 jar 一起放", "4. 启动并检查日志"]
        for i, line in enumerate(steps):
            self._text(p, QRectF(r.left() + 174, r.top() + 40 + i * 18, 140, 18), line, "muted", 7)


STEPS = [
    {
        "kind": "config",
        "title": "配置模型通道",
        "text": "打开「设置」，选择服务通道，填 Base URL、模型名和 API Key，点「测试连接」。"
                "连接正常后再开始，后面所有生成和编译都靠它。",
        "tip": "自定义中转地址会原样保留，不会被改回官方地址。",
        "action": ("去设置", "settings"),
    },
    {
        "kind": "project",
        "title": "新建工程，一键开始",
        "text": "点右上角「新建工程」，填 Mod ID、MC 版本、加载器和运行端。"
                "勾选「创建后立即让 AI 拆解完整规格」，创建后助手会先规划玩法、数值、美术和验收标准，生成规格说明书，确认后才写代码。",
        "tip": "三项基础信息缺一不可；Mod ID 只能是小写字母、数字、下划线。",
        "action": ("现在新建", "new"),
    },
    {
        "kind": "chat",
        "title": "用一句话描述需求",
        "text": "在输入框写清要什么，例如物品、方块、机器、生物，或者直接贴报错日志，按 Ctrl+Enter 发送。"
                "助手会先把需求拆成完整规格，右侧「规格」页会显示 10 项需求清单和说明书。",
        "tip": "检查注册内容、获取方式和数值；不满意就直接说，它会更新规格，不会提前写代码。",
        "action": ("去对话", "chat"),
    },
    {
        "kind": "pending",
        "title": "确认规格，开始施工",
        "text": "规格没问题就点「确认规格并开始施工」。助手会按骨架、注册、玩法、纹理、配方、语言、编译、验收的顺序逐项完成，"
                "右侧任务进度会实时前进；开启「自动写入」时文件直接落盘，关闭时先进入待写入列表。",
        "tip": "覆盖前都会自动备份；确认后也可以随时让助手更新规格。",
        "action": ("打开工程目录", "folder"),
    },
    {
        "kind": "build",
        "title": "编译、运行与成品说明书",
        "text": "打开「编译运行」页可以直接执行编译、开发客户端、开发服务端和数据生成。"
                "编译通过后，右侧「成品」页会读取 build/libs 里的真实 JAR，列出注册内容、配方、模型纹理和安装步骤。",
        "tip": "第一次运行会自动准备 JDK、Gradle Wrapper 和依赖，下载慢时会自动切换镜像。",
        "action": ("打开编译运行", "build"),
    },
    {
        "kind": "fix",
        "title": "失败自动修复",
        "text": "自动编译失败时，宿主会把 Gradle 日志作为下一条消息交给助手，让它按文件和行号修代码，"
                "修完再自动编译一次；仍然失败就停下来把完整日志给你，不会无限重试。",
        "tip": "最多连续自动编译两次；需要继续时点「继续」。",
        "action": ("查看编译日志", "build"),
    },
    {
        "kind": "output",
        "title": "拿到成品 jar",
        "text": "编译成功后产物在工程的 build/libs 下。把模组 jar 和它依赖的 jar 一起放进 .minecraft/mods，"
                "启动对应加载器（Fabric / NeoForge）即可加载。",
        "tip": "发布前建议先运行开发客户端，确认注册、资源和客户端/服务端分离都没有问题。",
        "action": ("打开产物目录", "libs"),
    },
]

SAMPLES = [
    ("发光矿石", "做一个新的发光矿石「星尘矿」，生成在主世界 0~40 层，挖掉后掉落星尘，"
                 "星尘可以合成一把自带微弱光照的剑。"),
    ("修复编译错误", "编译报错贴在这里：\n\n（粘贴 Gradle 报错日志）\n\n请定位根因并直接修复。"),
    ("新增机器", "新增一个消耗熔岩发电的方块机器，带 GUI、能量缓存和物品栏，"
                 "并给出合成配方和语言文件。"),
    ("纹理与 3D 模型", "给「星尘剑」画一个 16x16 的物品纹理，再做一个带护手和宝石的 3D 物品模型，"
                    "写好模型 JSON、lang 和合成配方，最后编译出包。"),
]


class TutorialPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        head = card()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(18, 14, 18, 14)
        info = QVBoxLayout()
        info.setSpacing(4)
        title = QLabel("从零到出包 · 图文教程")
        title.setObjectName("H1")
        info.addWidget(title)
        info.addWidget(
            tiny(
                f"恶魔小猫工坊 v{APP_VERSION}：配置一次模型，剩下的创建、写入、编译、修复和出包都能自动跑。"
            )
        )
        head_layout.addLayout(info, 1)
        self.start_btn = QPushButton("新建工程并开始")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._new_project)
        head_layout.addWidget(self.start_btn)
        root.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        host = QWidget()
        host.setObjectName("Window")
        self.steps_layout = QVBoxLayout(host)
        self.steps_layout.setContentsMargins(0, 0, 6, 0)
        self.steps_layout.setSpacing(10)
        for index, step in enumerate(STEPS, start=1):
            self.steps_layout.addWidget(self._step_card(index, step))
        self.steps_layout.addWidget(self._samples_card())
        self.steps_layout.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    def _step_card(self, index: int, step: dict) -> QWidget:
        frame = card()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(16)
        shot = Shot(step["kind"])
        shot.setAttribute(Qt.WA_StyledBackground, True)
        layout.addWidget(shot, 0, Qt.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(6)
        title = QLabel(f"{index:02d}  {step['title']}")
        title.setObjectName("H2")
        text.addWidget(title)
        body = QLabel(step["text"])
        body.setWordWrap(True)
        body.setMinimumWidth(360)
        text.addWidget(body)
        tip = tiny(step["tip"])
        text.addWidget(tip)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        label, action = step["action"]
        button = QPushButton(label)
        button.setObjectName("Ghost")
        button.clicked.connect(lambda _=False, name=action: self._act(name))
        button_row.addWidget(button)
        text.addLayout(button_row)
        layout.addLayout(text, 1)
        return frame

    def _samples_card(self) -> QWidget:
        frame = card()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel("常用需求模板")
        title.setObjectName("H2")
        layout.addWidget(title)
        layout.addWidget(tiny("点一下填进对话输入框，改完再发送。"))
        row = QHBoxLayout()
        row.setSpacing(8)
        for name, text in SAMPLES:
            button = QPushButton(name)
            button.setObjectName("Ghost")
            button.clicked.connect(lambda _=False, value=text: self._fill(value))
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        return frame

    # ------------------------------------------------------------------
    def _act(self, name: str) -> None:
        if name == "new":
            self._new_project()
            return
        if name == "settings":
            self.main.go_to(self.main.settings_page)
            return
        if name == "chat":
            self.main.go_to(self.main.chat_page)
            self.main.chat_page.input.setFocus()
            return
        if name == "build":
            self.main.go_to(self.main.build_page)
            return
        if name == "folder":
            project = self.main.chat_page.project
            if project is None:
                self.main.go_to(self.main.chat_page)
                self.main.status("先新建或打开一个工程。")
                return
            self.main.open_folder(project.root)
            return
        if name == "libs":
            project = self.main.chat_page.project
            if project is None:
                self.main.go_to(self.main.chat_page)
                self.main.status("先新建或打开一个工程。")
                return
            target = project.root / "build" / "libs"
            if not target.is_dir():
                target = project.root
            self.main.open_folder(target)

    def _new_project(self) -> None:
        self.main.go_to(self.main.chat_page)
        self.main.chat_page.new_project()

    def _fill(self, text: str) -> None:
        self.main.go_to(self.main.chat_page)
        page = self.main.chat_page
        page.input.setPlainText(text)
        page.input.setFocus()
        self.main.status("已填入示例需求，可以直接发送。")
