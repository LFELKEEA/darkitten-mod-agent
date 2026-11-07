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
        self._row(p, r.top() + 26, "MC 版本", "1.20.1")
        self._row(p, r.top() + 52, "加载器", "Fabric / Forge / NeoForge")
        p.setPen(QColor(C["ok"]))
        p.setBrush(QColor(C["ok"]))
        p.drawEllipse(QRectF(r.left() + 2, r.top() + 82, 8, 8))
        self._text(p, QRectF(r.left() + 16, r.top() + 77, 220, 18), "1.20.1 直接生成可编译骨架", "ok", 8)
        self._box(p, QRectF(r.right() - 100, r.bottom() - 22, 100, 22), "accent", None)
        self._text(p, QRectF(r.right() - 92, r.bottom() - 22, 88, 22), "创建并开始", "#fff", 8, True)

    def _draw_chat(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top(), 190, 34), "card_soft")
        self._text(p, QRectF(r.left() + 8, r.top() + 2, 175, 30),
                   "做一个会发光的矿石，挖掉回血", "text", 8)
        self._box(p, QRectF(r.right() - 205, r.top() + 42, 205, 44), "accent_soft")
        self._text(p, QRectF(r.right() - 197, r.top() + 44, 190, 18),
                   "正在写模组代码", "text", 8, True)
        self._text(p, QRectF(r.right() - 197, r.top() + 62, 190, 18),
                   "写完直接启动游戏验证", "ok", 8)
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
            ("> runServer  → 探针 127.0.0.1:39001", "accent"),
            ("> runClient  → 已进入世界 / 已连接", "accent"),
        ]
        for i, (line, color) in enumerate(lines):
            self._text(p, QRectF(r.left() + 10, r.top() + 8 + i * 22, r.width() - 20, 20),
                       line, color, 8, color in {"ok", "accent"})

    def _draw_fix(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top() + 12, 142, 78), "card_soft")
        self._text(p, QRectF(r.left() + 8, r.top() + 18, 128, 18), "assert:", "danger", 8, True)
        self._text(p, QRectF(r.left() + 8, r.top() + 38, 128, 18), "items 里没有星尘", "danger", 7)
        self._text(p, QRectF(r.left() + 8, r.top() + 56, 128, 18), "步骤 6 / 场景卡", "muted", 7)
        self._text(p, QRectF(r.left() + 148, r.top() + 34, 26, 30), "→", "accent", 15, True)
        self._box(p, QRectF(r.right() - 142, r.top() + 12, 142, 78), "card_soft")
        self._text(p, QRectF(r.right() - 134, r.top() + 18, 126, 18), "定位并改代码", "ok", 8, True)
        self._text(p, QRectF(r.right() - 134, r.top() + 40, 126, 18), "重跑场景", "muted", 7)
        self._text(p, QRectF(r.right() - 134, r.top() + 58, 126, 18), "全部通过", "ok", 7)
        self._text(p, QRectF(r.left(), r.bottom() - 16, r.width(), 16),
                   "断言失败会带出真实背包、界面和日志，不需要手贴", "muted", 7)

    def _draw_output(self, p: QPainter, r: QRectF) -> None:
        self._box(p, QRectF(r.left(), r.top() + 10, 158, 100), "card_soft")
        self._text(p, QRectF(r.left() + 10, r.top() + 18, 138, 18), "build/libs", "muted", 8)
        self._box(p, QRectF(r.left() + 10, r.top() + 42, 138, 24), "accent_soft")
        self._text(p, QRectF(r.left() + 18, r.top() + 42, 130, 24),
                   "stardust_tools-1.0.0.jar", "text", 7, True)
        self._text(p, QRectF(r.left() + 10, r.top() + 74, 138, 18), "复制到 .minecraft/mods", "muted", 7)
        self._text(p, QRectF(r.left() + 174, r.top() + 18, 140, 18), "安装步骤", "text", 8, True)
        steps = ["1. 关闭游戏", "2. 关闭工坊开发端", "3. 按工程加载器启动", "4. 放入 mods 并启动"]
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
        "title": "新建工程，直接拿到骨架",
        "text": "点右上角「新建工程」，选加载器（Fabric / Forge / NeoForge / Architectury）、MC 版本和运行端。"
                "1.20.1 的 Forge / Fabric / NeoForge 会直接生成可编译骨架；其他版本由助手核对官方模板后生成。",
        "tip": "Mod ID 只能是小写字母、数字、下划线，必须以字母开头。",
        "action": ("现在新建", "new"),
    },
    {
        "kind": "chat",
        "title": "用一句话描述需求",
        "text": "在输入框写清要什么：物品、方块、机器、生物、事件都行，按 Ctrl+Enter 发送。"
                "助手会直接读工程、改代码、补资源，不再先做一页规格让你确认。",
        "tip": "不确定写法时，让它先去知识库或真实代码检索里查对应加载器和版本的 API。",
        "action": ("去对话", "chat"),
    },
    {
        "kind": "pending",
        "title": "文件落盘",
        "text": "开启「自动写入」时，生成的文件直接写进工程，覆盖前自动备份；关闭时先进入右侧待写入列表，"
                "确认后再落盘。无论哪种方式，助手都会优先用精确替换，避免整份重写。",
        "tip": "写完可以随时让它编译，失败日志会带回对话里继续修。",
        "action": ("打开工程目录", "folder"),
    },
    {
        "kind": "build",
        "title": "启动真实开发端",
        "text": "对话页的「运行」面板可以直接启动开发服务端 / 客户端 / 双端。工坊先编译再执行 Gradle 运行任务；"
                "Forge 1.20.1 会额外安装游戏内探针，其他加载器直接看运行日志和 GameTest。",
        "tip": "首次启动会下载加载器和 Minecraft 依赖，需要几分钟；探针日志在工程的 .mcdev 目录。",
        "action": ("打开运行面板", "run"),
    },
    {
        "kind": "fix",
        "title": "在游戏里验证，而不是靠猜",
        "text": "Forge 1.20.1 的探针能读真实背包、界面、方块状态和截图，也能执行指令和点击；用场景卡片把"
                "「挖矿 → 掉落 → 合成」写成步骤和断言。其他加载器用 GameTest 和运行日志验证。失败时助手按真实数据改代码并重跑。",
        "tip": "GameTest 适合纯逻辑；Forge 1.20.1 涉及界面和渲染的玩法用客户端探针截图确认。",
        "action": ("查看运行日志", "run"),
    },
    {
        "kind": "output",
        "title": "拿到成品 jar",
        "text": "编译成功后产物在工程的 build/libs 下。把模组 jar 放进 .minecraft/mods，"
                "用对应加载器（Fabric / Forge / NeoForge）和匹配的 Minecraft 版本启动即可加载。",
        "tip": "发布前先跑一遍场景卡片，确认注册、资源和客户端 / 服务端分离都没有问题。",
        "action": ("打开产物目录", "libs"),
    },
]

SAMPLES = [
    ("发光矿石", "做一个新的发光矿石「星尘矿」，生成在主世界 0~40 层，挖掉后掉落星尘，"
                 "星尘可以合成一把自带微弱光照的剑。做完启动开发端挖一块验证掉落。"),
    ("修复编译错误", "编译报错贴在这里：\n\n（粘贴 Gradle 报错日志）\n\n请定位根因并直接修复。"),
    ("新增机器", "新增一个消耗熔岩发电的方块机器，带 GUI、能量缓存和物品栏，"
                 "并给出合成配方和语言文件。"),
    ("游戏内验证", "给「星尘剑」写一个场景卡片：服务端 give 一把星尘剑，客户端选中后截图，"
                 "断言背包里物品名称正确。"),
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
        if name == "run":
            self.main.go_to(self.main.chat_page)
            page = self.main.chat_page
            if hasattr(page, "side_tabs") and hasattr(page, "run_panel"):
                page.side_tabs.setCurrentWidget(page.run_panel)
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
