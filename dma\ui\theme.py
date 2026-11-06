from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#0f1117",
    "bg_alt": "#151823",
    "card": "#1a1e2b",
    "card_soft": "#202534",
    "line": "#2b3142",
    "text": "#e8eaf2",
    "muted": "#9299ad",
    "accent": "#8b7cff",
    "accent_soft": "#2a2650",
    "ok": "#4ec9a4",
    "warn": "#e0b45c",
    "danger": "#ef6b7a",
}


def stylesheet(font_size: int = 14) -> str:
    c = COLORS
    return f"""
QWidget {{
    color: {c['text']};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: {font_size}px;
}}
QMainWindow, QWidget#Window {{ background: {c['bg']}; }}
QWidget#Sidebar {{ background: {c['bg_alt']}; border-right: 1px solid {c['line']}; }}
QFrame#Card {{
    background: {c['card']};
    border: 1px solid {c['line']};
    border-radius: 12px;
}}
QFrame#CardSoft {{
    background: {c['card_soft']};
    border: 1px solid {c['line']};
    border-radius: 10px;
}}
QLabel#H1 {{ font-size: {font_size + 10}px; font-weight: 700; }}
QLabel#H2 {{ font-size: {font_size + 3}px; font-weight: 600; }}
QLabel#Tiny {{ color: {c['muted']}; font-size: {max(11, font_size - 2)}px; }}
QLabel#Accent {{ color: {c['accent']}; font-weight: 600; }}
QPushButton {{
    background: {c['card_soft']};
    border: 1px solid {c['line']};
    border-radius: 8px;
    padding: 7px 14px;
    min-height: 18px;
}}
QPushButton:hover {{ background: #272d3d; }}
QPushButton:pressed {{ background: #242938; }}
QPushButton:disabled {{ color: #5a6072; background: #191d27; border-color: #232837; }}
QPushButton#Primary {{
    background: {c['accent']};
    border: 1px solid {c['accent']};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: #9a8dff; }}
QPushButton#Primary:disabled {{ background: #433d78; border-color: #433d78; color: #b9b3e8; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid {c['line']}; }}
QPushButton#Ghost:hover {{ background: {c['card_soft']}; }}
QPushButton#Danger {{ color: {c['danger']}; }}
QPushButton#Danger:hover {{ background: #3a2028; }}
QPushButton#Nav {{
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 9px;
    background: transparent;
    color: {c['muted']};
    font-weight: 500;
}}
QPushButton#Nav:hover {{ background: {c['card']}; color: {c['text']}; }}
QPushButton#Nav:checked {{
    background: {c['accent_soft']};
    color: {c['text']};
    font-weight: 600;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {c['bg_alt']};
    border: 1px solid {c['line']};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {c['accent']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c['card']};
    border: 1px solid {c['line']};
    selection-background-color: {c['accent_soft']};
    outline: none;
}}
QTextBrowser {{
    background: {c['bg_alt']};
    border: 1px solid {c['line']};
    border-radius: 10px;
    padding: 10px;
}}
QListWidget, QTreeWidget, QTableWidget {{
    background: {c['bg_alt']};
    border: 1px solid {c['line']};
    border-radius: 10px;
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {c['accent_soft']};
    color: {c['text']};
}}
QHeaderView::section {{
    background: {c['card_soft']};
    border: none;
    border-bottom: 1px solid {c['line']};
    padding: 6px;
    color: {c['muted']};
}}
QTabWidget::pane {{ border: 1px solid {c['line']}; border-radius: 8px; }}
QTabBar::tab {{
    background: transparent;
    padding: 7px 14px;
    color: {c['muted']};
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom-color: {c['accent']}; }}
QProgressBar {{
    background: {c['bg_alt']};
    border: 1px solid {c['line']};
    border-radius: 6px;
    min-height: 10px;
    max-height: 10px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343b4e; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #414a61; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #343b4e; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: transparent; width: 6px; }}
QToolTip {{
    background: {c['card']};
    color: {c['text']};
    border: 1px solid {c['line']};
    padding: 4px 8px;
}}
QCheckBox {{ spacing: 8px; }}
QStatusBar {{
    background: {c['bg_alt']};
    color: {c['muted']};
    border-top: 1px solid {c['line']};
}}
QStatusBar::item {{ border: none; }}
QMessageBox, QDialog {{ background: {c['bg']}; }}
"""


def apply_theme(app: QApplication, font_size: int = 14) -> None:
    font = QFont("Microsoft YaHei UI", max(9, int(font_size)))
    app.setFont(font)
    app.setStyleSheet(stylesheet(font_size))
