"""Design system for the DDT Mirror workspace.

One dark, flat, modern theme applied app-wide: Fusion style as the base
(so no legacy Win32 chrome leaks through), design tokens below, and a
single QSS sheet. Buttons opt into emphasis with
setProperty("kind", "primary") / "accent".
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ------------------------------------------------------------- design tokens
BG = "#17181c"          # window background
SURFACE = "#1f2127"     # panels / inputs
SURFACE_2 = "#262931"   # hover / alternate rows
BORDER = "#33363f"
TEXT = "#e6e8ee"
TEXT_MUTED = "#8b90a0"
ACCENT = "#4f8cff"      # primary actions
ACCENT_HOVER = "#6ba1ff"
ACCENT_DOWN = "#3d74e0"
GREEN = "#34d399"
ORANGE = "#fbbf24"
RED = "#f87171"

MONO = "Cascadia Code, Consolas, monospace"

QSS = f"""
* {{
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 10pt;
    color: {TEXT};
}}
QMainWindow, QWidget {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel[class="muted"] {{ color: {TEXT_MUTED}; }}

/* ------------------------------------------------------------- buttons */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: {SURFACE_2}; border-color: #4a4e5a; }}
QPushButton:pressed {{ background: #14151a; }}
QPushButton:disabled {{ color: #5a5e6b; background: #1b1c21; }}
QPushButton[kind="primary"] {{
    background: {ACCENT};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_DOWN}; }}
QPushButton[kind="primary"]:disabled {{ background: #2b3a55; color: #7d8598; }}

/* -------------------------------------------------------------- inputs */
QLineEdit, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {TEXT_MUTED};
    margin-right: 10px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    outline: none;
}}

/* ---------------------------------------------------------------- tabs */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    top: -1px;
    background: {SURFACE};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 9px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* -------------------------------------------------------- lists / trees */
QTreeView, QListWidget, QPlainTextEdit, QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
    alternate-background-color: {SURFACE_2};
    outline: none;
    padding: 4px;
}}
QTreeView::item, QListWidget::item {{ padding: 4px 2px; }}
QTreeView::item:selected, QListWidget::item:selected {{
    background: {ACCENT};
    color: white;
    border-radius: 4px;
}}
QTreeView::item:hover:!selected, QListWidget::item:hover:!selected {{
    background: {SURFACE_2};
}}
QHeaderView::section {{
    background: {BG};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 600;
}}
QTreeView::branch {{ background: transparent; }}

/* checkboxes (incl. tree/list check indicators) */
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTreeView::indicator, QListWidget::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked, QTreeView::indicator:checked,
QListWidget::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: url(none);
}}
QTreeView::indicator:indeterminate {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {SURFACE});
    border-color: {ACCENT};
}}

/* ------------------------------------------------------------ scrollbars */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #3d414d; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #4d5261; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #3d414d; border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: #4d5261; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------------------------------------------------------------- misc */
QSplitter::handle {{ background: {BG}; width: 6px; }}
QSplitter::handle:hover {{ background: {BORDER}; }}
QStatusBar {{
    background: {SURFACE};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}
QToolTip {{
    background: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
}}
QMessageBox, QInputDialog, QFileDialog {{ background: {SURFACE}; }}

/* preview panes read better in monospace */
QPlainTextEdit[class="mono"] {{
    font-family: {MONO};
    font-size: 9.5pt;
}}
"""


def apply_theme(app: QApplication) -> None:
    """Fusion base + dark palette + the QSS above."""
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(SURFACE))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE_2))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(SURFACE))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ToolTipBase, QColor(SURFACE_2))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_MUTED))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
