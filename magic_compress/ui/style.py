"""Application theming: light and dark, built on Qt's Fusion style."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# Colour tokens for each theme. The QSS template and QPalette are both built
# from these, so a theme is fully described by one dict.
LIGHT = {
    "bg": "#f4f5f7", "base": "#ffffff", "alt": "#fafbfc",
    "text": "#26292e", "muted": "#55606e",
    "border": "#e3e6ea", "header_bg": "#f7f8fa",
    "accent": "#3a6df5", "accent_hover": "#2f5fe0",
    "hover": "#eef1f6", "pressed": "#e2e7f0",
    "sel_bg": "#dbe6ff", "sel_text": "#16181c",
    "input_bg": "#ffffff", "input_border": "#d4d9e0",
    "disabled_text": "#aab0b8", "disabled_bg": "#f0f1f3",
    "track": "#eef1f5", "groove": "#d4d9e0",
}

DARK = {
    "bg": "#1b1e24", "base": "#23272e", "alt": "#262b33",
    "text": "#e6e9ee", "muted": "#9aa4b2",
    "border": "#333a44", "header_bg": "#262b33",
    "accent": "#5b8dff", "accent_hover": "#4a7cf0",
    "hover": "#2d333d", "pressed": "#343b46",
    "sel_bg": "#33405c", "sel_text": "#f2f5fa",
    "input_bg": "#1f242b", "input_border": "#3a414c",
    "disabled_text": "#667080", "disabled_bg": "#22262d",
    "track": "#2a2f38", "groove": "#3a414c",
}


def _qss(c: dict) -> str:
    return f"""
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget {{ color: {c['text']}; }}
QToolBar {{
    background: {c['base']}; border: none;
    border-bottom: 1px solid {c['border']};
    padding: 6px 8px; spacing: 4px;
}}
QToolBar QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 6px 10px; margin: 0 1px; color: {c['text']}; font-size: 12px;
}}
QToolBar QToolButton:hover {{ background: {c['hover']}; border-color: {c['border']}; }}
QToolBar QToolButton:pressed {{ background: {c['pressed']}; }}
QToolBar QToolButton:disabled {{ color: {c['disabled_text']}; }}
QToolBar::separator {{ background: {c['border']}; width: 1px; margin: 4px 6px; }}
QMenuBar {{ background: {c['base']}; color: {c['text']}; }}
QMenuBar::item:selected {{ background: {c['hover']}; }}
QMenu {{ background: {c['base']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['sel_bg']}; color: {c['sel_text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QTreeView {{
    background: {c['base']}; border: 1px solid {c['border']}; border-radius: 10px;
    outline: none; selection-background-color: {c['sel_bg']};
    selection-color: {c['sel_text']}; alternate-background-color: {c['alt']};
}}
QTreeView::item {{ padding: 4px 2px; min-height: 22px; }}
QTreeView::item:selected {{ background: {c['sel_bg']}; color: {c['sel_text']}; }}
QHeaderView::section {{
    background: {c['header_bg']}; color: {c['muted']}; padding: 6px 8px; border: none;
    border-right: 1px solid {c['border']}; border-bottom: 1px solid {c['border']};
    font-weight: 600;
}}
QStatusBar {{ background: {c['base']}; border-top: 1px solid {c['border']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
QLabel#Breadcrumb {{ color: {c['muted']}; padding: 8px 12px; font-size: 12px; }}
QPushButton {{
    background: {c['base']}; border: 1px solid {c['input_border']}; border-radius: 8px;
    padding: 7px 16px; color: {c['text']};
}}
QPushButton:hover {{ background: {c['hover']}; }}
QPushButton:default, QPushButton#Primary {{
    background: {c['accent']}; border-color: {c['accent']}; color: #ffffff; font-weight: 600;
}}
QPushButton:default:hover, QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
QPushButton:disabled {{ color: {c['disabled_text']}; background: {c['disabled_bg']}; border-color: {c['border']}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {c['input_bg']}; border: 1px solid {c['input_border']}; border-radius: 8px;
    padding: 6px 9px; color: {c['text']}; selection-background-color: {c['sel_bg']};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c['base']}; border: 1px solid {c['border']};
    selection-background-color: {c['sel_bg']}; selection-color: {c['sel_text']};
}}
QProgressBar {{
    border: 1px solid {c['border']}; border-radius: 8px; background: {c['track']};
    height: 18px; text-align: center; color: {c['text']};
}}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 7px; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['groove']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {c['base']}; border: 2px solid {c['accent']};
    width: 14px; height: 14px; margin: -7px 0; border-radius: 9px;
}}
QListWidget {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; }}
QGroupBox {{
    border: 1px solid {c['border']}; border-radius: 10px; margin-top: 10px;
    padding: 12px; font-weight: 600; color: {c['muted']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
QCheckBox {{ color: {c['text']}; }}
QMessageBox {{ background: {c['bg']}; }}
"""


def _palette(c: dict) -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["base"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["base"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor(c["base"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(c["disabled_text"]))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["disabled_text"]))
    return pal


def apply_theme(app: QApplication, dark: bool = False) -> None:
    app.setStyle("Fusion")
    colors = DARK if dark else LIGHT
    app.setPalette(_palette(colors))
    app.setStyleSheet(_qss(colors))
