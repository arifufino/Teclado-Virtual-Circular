"""Ventana principal y controlador de UI para Circular Keyboard."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from math import cos, pi, sin

from pynput.keyboard import Controller
from PyQt6.QtCore import QPoint, QSize, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QWidget
from symspellpy import SymSpell, Verbosity

import win32gui

_keyboard_controller = Controller()


# === SymSpell setup ===
_sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
_dictionary_path = os.path.join(os.path.dirname(__file__), "es_full.txt")
_dict_loaded = _sym_spell.load_dictionary(_dictionary_path, term_index=0, count_index=1, encoding="utf-8")
print(f"DEBUG: ruta={_dictionary_path}")
print(f"DEBUG: existe archivo={os.path.exists(_dictionary_path)}")
print(f"DEBUG: diccionario cargado={_dict_loaded}, entradas={len(_sym_spell.words)}")
print(f"DEBUG: 'hola' en dict? {'hola' in _sym_spell.words}")
print(f"DEBUG: 'perro' en dict? {'perro' in _sym_spell.words}")


# === SendInput setup para envío directo sin robar foco ===
PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class Input_I(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", Input_I)]


INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_BACK = 0x08


def _send_unicode_char(char: str) -> None:
    """Envía un carácter unicode mediante SendInput sin cambiar el foco."""
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()

    # Key down
    ii_.ki = KeyBdInput(0, ord(char), KEYEVENTF_UNICODE, 0, ctypes.pointer(extra))
    x = Input(INPUT_KEYBOARD, ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    # Key up
    ii_.ki = KeyBdInput(0, ord(char), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    x = Input(INPUT_KEYBOARD, ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))


def _send_backspace() -> None:
    """Envía un Backspace mediante SendInput sin cambiar el foco."""
    extra = ctypes.c_ulong(0)
    ii_ = Input_I()

    # Key down
    ii_.ki = KeyBdInput(VK_BACK, 0, 0, 0, ctypes.pointer(extra))
    x = Input(INPUT_KEYBOARD, ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    # Key up
    ii_.ki = KeyBdInput(VK_BACK, 0, KEYEVENTF_KEYUP, 0, ctypes.pointer(extra))
    x = Input(INPUT_KEYBOARD, ii_)
    ctypes.windll.user32.SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

def _autocomplete_word(prefix: str) -> str | None:
    """Busca la palabra más frecuente del diccionario que empiece con `prefix`.

    Devuelve None si el prefijo ya es una palabra completa y válida,
    o si no hay coincidencias mejores.
    """
    if not prefix:
        return None

    prefix_lower = prefix.lower()

    # Si la palabra ya existe tal cual en el diccionario, no autocompletar.
    if prefix_lower in _sym_spell.words:
        return None

    best_word = None
    best_freq = -1
    for candidate, freq in _sym_spell.words.items():
        if len(candidate) <= len(prefix_lower):
            continue
        if candidate.startswith(prefix_lower) and freq > best_freq:
            best_word = candidate
            best_freq = freq

    return best_word

class CircularButtonPanel(QWidget):
    WINDOW_SIZE = 500
    BUTTON_SIZE = 50
    MARGIN = 36
    BUTTON_MARGIN = 18
    SPACE_BUTTON_WIDTH_FACTOR = 3.8

    def __init__(
        self,
        ring_letters: list[str],
        center_letters: list[str],
        button_diameter: int = BUTTON_SIZE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ring_letters = ring_letters
        self._center_letters = center_letters
        self._button_diameter = button_diameter
        self._ring_buttons: list[QPushButton] = []
        self._center_buttons: list[QPushButton] = []
        self._space_button: QPushButton | None = None
        self._last_foreground_hwnd: int | None = None
        self._current_word: str = ""
        self._current_word: str = ""
        self._preview_label = QLabel("", self)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(
            "color: #aaaaaa;"
            "font-size: 13px;"
            "font-style: italic;"
            "background: transparent;"
        )
        self._preview_label.setFixedHeight(20)
        self._create_buttons()

    def _create_buttons(self) -> None:
        for letter in self._ring_letters:
            self._ring_buttons.append(self._create_button(letter))

        for letter in self._center_letters:
            self._center_buttons.append(self._create_button(letter, accent=True))

        self._space_button = self._create_button("ESPACIO", rectangular=True)
        self._close_button = self._create_button("X", accent=True)
        self._close_button.clicked.connect(lambda: QApplication.quit())

    def _create_button(self, text: str, accent: bool = False, rectangular: bool = False) -> QPushButton:
        display_text = text.lower()
        button = QPushButton(display_text, self)

        if rectangular:
            button.setFixedSize(QSize(
                int(self._button_diameter * self.SPACE_BUTTON_WIDTH_FACTOR),
                int(self._button_diameter * 1.15)
            ))
        else:
            button.setFixedSize(QSize(self._button_diameter, self._button_diameter))

        border_radius = int(self._button_diameter / 2) if not rectangular else 6
        background_color = "#363636" if rectangular else ("#D18E5F" if accent else "#5A5059")
        border_color = "#92887F" if not accent else "#B8ADAB"
        hover_color = "#92887F" if not accent else "#E0A878"
        text_color = "#D7EEF3" if not accent else "#3A2415"
        font_weight = "600" if rectangular else "400"

        button.setStyleSheet(
            f"QPushButton {{"
            f"background-color: {background_color};"
            f"color: {text_color};"
            f"border: 2px solid {border_color};"
            f"border-radius: {border_radius}px;"
            f"font-size: 14px;"
            f"font-weight: {font_weight};"
            f"}}"
            f"QPushButton:hover {{"
            f"background-color: {hover_color};"
            f"}}"
        )

        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda checked=False, v=display_text: self._on_button_clicked(v))
        return button

    # === Layout methods ===
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_buttons()

    def _layout_buttons(self):
        w = self.width()
        h = self.height()
        cx, cy = w / 2, h / 2
        self._layout_ring(cx, cy)
        self._layout_center_group(cx, cy)
        self._layout_space_button(cx, cy)
        self._layout_close_button(cx, cy)
        self._layout_preview_label(cx, cy)

    def _layout_ring(self, center_x: float, center_y: float):
        count = len(self._ring_buttons)
        if count == 0:
            return
        outer_radius = min(self.width(), self.height()) / 2 - self.MARGIN - self._button_diameter / 2
        required_radius = (self._button_diameter + self.BUTTON_MARGIN) / (2 * sin(pi / count))
        radius = min(outer_radius, max(required_radius, outer_radius * 0.92))

        for index, button in enumerate(self._ring_buttons):
            angle = 2 * pi * index / count - (pi / 2) + 0.12
            x = center_x + radius * cos(angle) - self._button_diameter / 2
            y = center_y + radius * sin(angle) - self._button_diameter / 2
            button.move(int(x), int(y))

    def _layout_center_group(self, center_x: float, center_y: float):
        if not self._center_buttons:
            return
        btn = self._button_diameter
        s = btn * 1.12
        positions = [
            (-s * 0.9, -btn * 1.85),   # A
            ( s * 0.9, -btn * 1.85),   # E
            (-s * 1.05, -btn * 0.55),  # I
            (0,         -btn * 0.55),  # O
            ( s * 1.05, -btn * 0.55),  # U
        ]
        for button, (dx, dy) in zip(self._center_buttons, positions):
            x = center_x + dx - btn / 2
            y = center_y + dy - btn / 2
            button.move(int(x), int(y))

    def _layout_space_button(self, center_x: float, center_y: float):
        if not self._space_button:
            return
        w = self._space_button.width()
        x = center_x - w / 2
        y = center_y + self._button_diameter * 0.75
        self._space_button.move(int(x), int(y))

    def _layout_close_button(self, center_x: float, center_y: float):
        if not getattr(self, "_close_button", None):
            return
        btn = self._button_diameter
        x = center_x + btn * 3.2 - btn / 2
        y = center_y - self.height() / 2 + self.MARGIN
        self._close_button.move(int(x), int(y))

    def _layout_preview_label(self, center_x: float, center_y: float):
        if not getattr(self, "_preview_label", None):
            return
        label_width = 200
        self._preview_label.setFixedWidth(label_width)
        x = center_x - label_width / 2
        y = self.height() - self.MARGIN - 100 #Cambiar posicion de arriba hacia abajo texto sugerido
        self._preview_label.move(int(x), int(y))    

    # === Lógica de foco: nunca cambiamos la ventana activa ===
    def enterEvent(self, event):
        try:
            hwnd = win32gui.GetForegroundWindow()
            my_hwnd = int(self.window().winId())
            if hwnd != my_hwnd:
                self._last_foreground_hwnd = hwnd
        except Exception:
            self._last_foreground_hwnd = None
        super().enterEvent(event)

    def _on_button_clicked(self, value: str):
        target_hwnd = self._last_foreground_hwnd

        try:
            if not (target_hwnd and win32gui.IsWindow(target_hwnd)):
                print("No target window found")
                return

            if value.lower() == "espacio":
                self._autocorrect_and_send_space()
            else:
                char = value.lower()
                _send_unicode_char(char)
                self._current_word += char
                self._update_preview()
        except Exception as e:
            print(f"Error sending key: {e}")

    def _update_preview(self) -> None:
        word = self._current_word
        if not word:
            self._preview_label.setText("")
            return

        completion = _autocomplete_word(word)
        if completion:
            self._preview_label.setText(f"{word} → {completion}")
        else:
            self._preview_label.setText(word)

    def _autocorrect_and_send_space(self) -> None:
        word = self._current_word

        if word:
            completion = _autocomplete_word(word)
            if completion:
                self._replace_last_word(word, completion)
            elif len(word) >= 3:
                suggestions = _sym_spell.lookup(word, Verbosity.TOP, max_edit_distance=2)
                if suggestions and suggestions[0].term != word:
                    corrected = suggestions[0].term
                    self._replace_last_word(word, corrected)

        _send_unicode_char(" ")
        self._current_word = ""
        self._preview_label.setText("")

    def _replace_last_word(self, old_word: str, new_word: str) -> None:
        # Borra los caracteres de la palabra incorrecta
        for _ in range(len(old_word)):
            _send_backspace()
        # Escribe la palabra corregida
        for char in new_word:
            _send_unicode_char(char)


class MainWindow(QMainWindow):
    _CONSONANTS = [
        "B", "C", "D", "F", "G", "H", "J", "K", "L", "M", "N", "Ñ",
        "P", "Q", "R", "S", "T", "V", "W", "X", "Y", "Z",
    ]
    _VOWELS = ["A", "E", "I", "O", "U"]

    def __init__(self) -> None:
        super().__init__()
        self._drag_position: QPoint | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        self.setWindowTitle("Circular Keyboard")
        self.resize(500, 500)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(0.75)

        central_widget = QWidget(self)
        central_widget.setObjectName("central_widget")
        central_widget.setStyleSheet(
            "#central_widget {"
            "background-color: #2A2630;"
            "border: 1px solid #5A5059;"
            "border-radius: 30px;"
            "}"
        )

        self._button_panel = CircularButtonPanel(
            ring_letters=self._CONSONANTS,
            center_letters=self._VOWELS,
            button_diameter=CircularButtonPanel.BUTTON_SIZE,
            parent=central_widget,
        )

        central_widget.resize(self.size())
        self.setCentralWidget(central_widget)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if central := self.centralWidget():
            central.resize(self.size())
            if panel := getattr(self, "_button_panel", None):
                panel.resize(self.size())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_position = None
        super().mouseReleaseEvent(event)