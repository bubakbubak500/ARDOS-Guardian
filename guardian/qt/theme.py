"""Guardian Monitor theme, aligned with the neighboring Modeling Anten app."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class ThemePreference(StrEnum):
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    application_background: str
    surface_1: str
    surface_2: str
    surface_3: str
    panel_border: str
    divider: str
    text_primary: str
    text_secondary: str
    text_muted: str
    text_inverse: str
    accent: str
    accent_hover: str
    accent_pressed: str
    success: str
    warning: str
    danger: str
    info: str
    selected: str
    hovered: str
    disabled: str
    focused: str
    spacing_1: int = 4
    spacing_2: int = 8
    spacing_3: int = 12
    spacing_4: int = 16
    radius_small: int = 2
    radius_medium: int = 4
    ui_font_px: int = 12
    heading_font_px: int = 14
    metadata_font_px: int = 10


LIGHT_TOKENS = ThemeTokens(
    application_background="#e9edf1",
    surface_1="#ffffff",
    surface_2="#f5f7f9",
    surface_3="#e6ebef",
    panel_border="#b8c3cc",
    divider="#cbd3da",
    text_primary="#17232e",
    text_secondary="#3f5363",
    text_muted="#607483",
    text_inverse="#ffffff",
    accent="#007f82",
    accent_hover="#00696c",
    accent_pressed="#176f88",
    success="#23733d",
    warning="#8a5a00",
    danger="#a72c36",
    info="#1769a6",
    selected="#ccebee",
    hovered="#e4f1f2",
    disabled="#7b8994",
    focused="#007f82",
)

DARK_TOKENS = ThemeTokens(
    application_background="#0b1118",
    surface_1="#111922",
    surface_2="#16212c",
    surface_3="#1c2a36",
    panel_border="#2b3b49",
    divider="#243441",
    text_primary="#edf3f7",
    text_secondary="#aebdca",
    text_muted="#8193a3",
    text_inverse="#0b1118",
    accent="#2cc7c9",
    accent_hover="#56d9d8",
    accent_pressed="#45b9c8",
    success="#55b77a",
    warning="#d1a44b",
    danger="#df6b72",
    info="#65a8e8",
    selected="#183e49",
    hovered="#1c303b",
    disabled="#61717e",
    focused="#63dce0",
)


def _system_is_dark(application: QApplication) -> bool:
    style_hints = application.styleHints()
    color_scheme = getattr(style_hints, "colorScheme", None)
    if color_scheme is not None:
        return color_scheme() == Qt.ColorScheme.Dark
    return application.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _palette(tokens: ThemeTokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.application_background,
        QPalette.ColorRole.WindowText: tokens.text_primary,
        QPalette.ColorRole.Base: tokens.surface_1,
        QPalette.ColorRole.AlternateBase: tokens.surface_2,
        QPalette.ColorRole.ToolTipBase: tokens.surface_3,
        QPalette.ColorRole.ToolTipText: tokens.text_primary,
        QPalette.ColorRole.Text: tokens.text_primary,
        QPalette.ColorRole.Button: tokens.surface_2,
        QPalette.ColorRole.ButtonText: tokens.text_primary,
        QPalette.ColorRole.BrightText: tokens.danger,
        QPalette.ColorRole.Highlight: tokens.selected,
        QPalette.ColorRole.HighlightedText: tokens.text_primary,
        QPalette.ColorRole.Link: tokens.accent,
        QPalette.ColorRole.PlaceholderText: tokens.text_muted,
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(tokens.disabled),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(tokens.disabled),
    )
    return palette


def stylesheet(tokens: ThemeTokens) -> str:
    return f"""
* {{ font-size: {tokens.ui_font_px}px; }}
QWidget {{ color: {tokens.text_primary}; background: {tokens.application_background}; }}
QMainWindow, QDialog {{ background: {tokens.application_background}; }}
QMenuBar {{
    background: {tokens.surface_1};
    border-bottom: 1px solid {tokens.panel_border};
    padding: {tokens.spacing_1}px;
}}
QMenuBar::item, QMenu::item {{ padding: {tokens.spacing_2}px {tokens.spacing_4}px; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {tokens.hovered}; }}
QMenu {{ background: {tokens.surface_1}; border: 1px solid {tokens.panel_border}; }}
QFrame#OperationalHeader, QFrame#MetricStrip, QFrame#StatusStrip,
QFrame#ReadinessPanel, QFrame#ActivityPanel, QFrame#WorkspacePanel {{
    background: {tokens.surface_1};
    border: 1px solid {tokens.panel_border};
    border-radius: {tokens.radius_medium}px;
}}
QLabel#SectionLabel {{
    color: {tokens.text_secondary};
    font-size: {tokens.metadata_font_px}px;
    font-weight: 600;
}}
QLabel#PanelHeader {{ font-size: {tokens.heading_font_px}px; font-weight: 600; }}
QLabel#ContextValue, QLabel#MetricValue {{ font-weight: 600; }}
QLabel#MetricLabel, QLabel#Metadata {{
    color: {tokens.text_secondary};
    font-size: {tokens.metadata_font_px}px;
}}
QLabel[statusRole="success"] {{ color: {tokens.success}; font-weight: 600; }}
QLabel[statusRole="warning"] {{ color: {tokens.warning}; font-weight: 600; }}
QLabel[statusRole="danger"] {{ color: {tokens.danger}; font-weight: 600; }}
QLabel[statusRole="info"] {{ color: {tokens.info}; font-weight: 600; }}
QLabel[statusRole="inactive"] {{ color: {tokens.text_secondary}; }}
QPushButton, QToolButton, QComboBox {{
    min-height: 22px;
    padding: {tokens.spacing_1}px {tokens.spacing_3}px;
    background: {tokens.surface_2};
    border: 1px solid {tokens.panel_border};
    border-radius: {tokens.radius_small}px;
}}
QPushButton:hover, QToolButton:hover, QComboBox:hover {{
    background: {tokens.hovered};
    border-color: {tokens.accent};
}}
QPushButton:focus, QToolButton:focus, QComboBox:focus {{
    border: 1px solid {tokens.focused};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {tokens.disabled};
    background: {tokens.surface_1};
}}
QPushButton#primaryAction {{
    min-height: 26px;
    background: {tokens.accent};
    color: {tokens.text_inverse};
    border-color: {tokens.accent};
    font-weight: 600;
}}
QPushButton#primaryAction:hover {{ background: {tokens.accent_hover}; }}
QPushButton#primaryAction:pressed {{ background: {tokens.accent_pressed}; }}
QPlainTextEdit {{
    background: {tokens.surface_1};
    border: 1px solid {tokens.panel_border};
    border-radius: {tokens.radius_small}px;
    selection-background-color: {tokens.selected};
}}
QTreeWidget {{
    background: {tokens.surface_1};
    alternate-background-color: {tokens.surface_2};
    border: 1px solid {tokens.panel_border};
    selection-background-color: {tokens.selected};
}}
QHeaderView::section {{
    background: {tokens.surface_3};
    color: {tokens.text_secondary};
    border: 0;
    border-right: 1px solid {tokens.divider};
    border-bottom: 1px solid {tokens.panel_border};
    padding: {tokens.spacing_2}px {tokens.spacing_3}px;
    font-weight: 600;
}}
QSplitter::handle {{ background: {tokens.divider}; width: 5px; }}
QStatusBar {{ background: {tokens.surface_1}; border-top: 1px solid {tokens.panel_border}; }}
QToolTip {{
    color: {tokens.text_primary};
    background: {tokens.surface_3};
    border: 1px solid {tokens.focused};
    padding: {tokens.spacing_2}px;
}}
"""


class ThemeController(QObject):
    theme_changed = Signal(object)

    def __init__(self, settings: QSettings, parent: QObject | None = None):
        super().__init__(parent)
        self.settings = settings
        self.application = QApplication.instance()
        if self.application is None:
            raise RuntimeError("ThemeController requires a QApplication")
        signal = getattr(self.application.styleHints(), "colorSchemeChanged", None)
        if signal is not None:
            signal.connect(self._system_theme_changed)
        self.apply()

    @property
    def preference(self) -> ThemePreference:
        value = str(self.settings.value("ui/theme", ThemePreference.SYSTEM.value))
        try:
            return ThemePreference(value)
        except ValueError:
            return ThemePreference.SYSTEM

    @property
    def effective_theme(self) -> ThemePreference:
        if self.preference != ThemePreference.SYSTEM:
            return self.preference
        return (
            ThemePreference.DARK
            if _system_is_dark(self.application)
            else ThemePreference.LIGHT
        )

    @property
    def tokens(self) -> ThemeTokens:
        return (
            DARK_TOKENS
            if self.effective_theme == ThemePreference.DARK
            else LIGHT_TOKENS
        )

    def set_preference(self, preference: ThemePreference | str) -> None:
        value = ThemePreference(preference)
        self.settings.setValue("ui/theme", value.value)
        self.settings.sync()
        self.apply()

    def apply(self) -> None:
        tokens = self.tokens
        self.application.setPalette(_palette(tokens))
        self.application.setStyleSheet(stylesheet(tokens))
        self.theme_changed.emit(tokens)

    def _system_theme_changed(self, _scheme) -> None:
        if self.preference == ThemePreference.SYSTEM:
            self.apply()
