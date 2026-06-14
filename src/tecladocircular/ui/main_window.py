"""Ventana principal y controlador de UI para Circular Keyboard."""

from __future__ import annotations

import ctypes
import os
import threading
import traceback
from ctypes import wintypes
from datetime import datetime
from math import cos, pi, sin

from pynput.keyboard import Controller
from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from symspellpy import SymSpell, Verbosity

import win32gui

try:
    import speech_recognition as sr
except ImportError:
    sr = None

_keyboard_controller = Controller()


# === Debug log para diagnóstico del micrófono ===
_LOG_PATH = os.path.join(os.path.dirname(__file__), "mic_debug.log")


def _mic_log(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# === SymSpell setup ===
_sym_spell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
_dictionary_path = os.path.join(os.path.dirname(__file__), "es_full.txt")
_dict_loaded = _sym_spell.load_dictionary(_dictionary_path, term_index=0, count_index=1, encoding="utf-8")
print(f"DEBUG: ruta={_dictionary_path}")
print(f"DEBUG: existe archivo={os.path.exists(_dictionary_path)}")
print(f"DEBUG: diccionario cargado={_dict_loaded}, entradas={len(_sym_spell.words)}")


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


def _send_text(text: str) -> None:
    """Envía una cadena completa de texto mediante SendInput sin cambiar el foco."""
    for char in text:
        if char == "\n":
            continue
        _send_unicode_char(char)


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


class RippleOverlay(QWidget):
    """Overlay transparente que dibuja un anillo expandiéndose desde el centro
    del teclado hacia un punto objetivo (el botón presionado)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._origin = QPoint(0, 0)
        self._target = QPoint(0, 0)
        self._progress = 0.0
        self._color = QColor("#fab387")
        self._animation = QPropertyAnimation(self, b"progress")
        self._animation.setDuration(260)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_finished)

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = property(get_progress, set_progress)  # type: ignore[assignment]

    def trigger(self, origin: QPoint, target: QPoint, color: QColor) -> None:
        self._origin = origin
        self._target = target
        self._color = color
        self._animation.stop()
        self._animation.start()

    def _on_finished(self) -> None:
        self._progress = 0.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._progress <= 0.0 or self._progress >= 1.0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dx = self._target.x() - self._origin.x()
        dy = self._target.y() - self._origin.y()
        current = QPoint(
            int(self._origin.x() + dx * self._progress),
            int(self._origin.y() + dy * self._progress),
        )

        max_radius = 26
        radius = max_radius * (1.0 - self._progress * 0.35)
        alpha = max(0, int(160 * (1.0 - self._progress)))

        color = QColor(self._color)
        color.setAlpha(alpha)

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(current, int(radius), int(radius))


class CircularButtonPanel(QWidget):
    """Pestaña de teclado: anillo de consonantes, vocales, espacio y cierre."""

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
        self._button_animations: dict[QPushButton, QPropertyAnimation] = {}

        self._preview_label = QLabel("", self)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(
            "color: #a6adc8;"
            "font-size: 13px;"
            "font-style: italic;"
            "background: transparent;"
        )
        self._preview_label.setFixedHeight(20)

        self._create_buttons()

        # Overlay de ripple por encima de todos los botones.
        self._ripple_overlay = RippleOverlay(self)
        self._ripple_overlay.raise_()

    def _create_buttons(self) -> None:
        for letter in self._ring_letters:
            self._ring_buttons.append(self._create_button(letter))

        for letter in self._center_letters:
            self._center_buttons.append(self._create_button(letter, accent=True))

        self._space_button = self._create_button("ESPACIO", rectangular=True)

        self._close_button = self._create_button("X", accent=True)
        self._close_button.setStyleSheet(
            "QPushButton {"
            "background-color: #313244;"
            "color: #f38ba8;"
            "border: 2px solid #f38ba8;"
            "border-radius: 15px;"
            "font-size: 14px;"
            "font-weight: 600;"
            "}"
            "QPushButton:hover {"
            "background-color: #45475a;"
            "}"
        )
        self._close_button.clicked.disconnect()
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
        background_color = "#45475a" if rectangular else ("#fab387" if accent else "#313244")
        border_color = "#585b70" if not accent else "#f9e2af"
        hover_color = "#585b70" if not accent else "#fad6b8"
        text_color = "#cdd6f4" if not accent else "#11111b"
        font_weight = "600" if rectangular else "500"

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
        button.clicked.connect(lambda checked=False, v=display_text, b=button: self._on_button_clicked(v, b))
        return button

    # === Layout methods ===
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_buttons()
        if getattr(self, "_ripple_overlay", None):
            self._ripple_overlay.setGeometry(0, 0, self.width(), self.height())
            self._ripple_overlay.raise_()

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
            self._set_base_geometry(button, int(x), int(y), self._button_diameter, self._button_diameter)

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
            self._set_base_geometry(button, int(x), int(y), btn, btn)

    def _layout_space_button(self, center_x: float, center_y: float):
        if not self._space_button:
            return
        w = self._space_button.width()
        h = self._space_button.height()
        x = center_x - w / 2
        y = center_y + self._button_diameter * 0.75
        self._set_base_geometry(self._space_button, int(x), int(y), w, h)

    def _layout_close_button(self, center_x: float, center_y: float):
        if not getattr(self, "_close_button", None):
            return
        btn = self._button_diameter
        x = center_x + btn * 3.2 - btn / 2
        y = center_y - self.height() / 2 + self.MARGIN
        self._set_base_geometry(self._close_button, int(x), int(y), btn, btn)

    def _layout_preview_label(self, center_x: float, center_y: float):
        if not getattr(self, "_preview_label", None):
            return
        label_width = 200
        self._preview_label.setFixedWidth(label_width)
        x = center_x - label_width / 2
        y = self.height() - self.MARGIN - 100
        self._preview_label.move(int(x), int(y))

    # === Geometría base (para animaciones) ===
    def _set_base_geometry(self, button: QPushButton, x: int, y: int, w: int, h: int) -> None:
        """Mueve el botón a su posición/tamaño base, cancelando cualquier
        animación de pulsación en curso para evitar conflictos al redimensionar."""
        anim = self._button_animations.get(button)
        if anim is not None and anim.state() == QPropertyAnimation.State.Running:
            anim.stop()
        button.setProperty("_base_geometry", QRect(x, y, w, h))
        button.setGeometry(x, y, w, h)

    # === Animación de pulsación (bounce) ===
    def _animate_press(self, button: QPushButton) -> None:
        base_rect: QRect | None = button.property("_base_geometry")
        if base_rect is None:
            base_rect = button.geometry()
            button.setProperty("_base_geometry", base_rect)

        existing = self._button_animations.get(button)
        if existing is not None and existing.state() == QPropertyAnimation.State.Running:
            existing.stop()
            button.setGeometry(base_rect)

        grow = 4
        expanded_rect = QRect(
            base_rect.x() - grow,
            base_rect.y() - grow,
            base_rect.width() + grow * 2,
            base_rect.height() + grow * 2,
        )

        animation = QPropertyAnimation(button, b"geometry")
        animation.setDuration(180)
        animation.setKeyValueAt(0.0, base_rect)
        animation.setKeyValueAt(0.45, expanded_rect)
        animation.setKeyValueAt(1.0, base_rect)
        animation.setEasingCurve(QEasingCurve.Type.OutInQuad)
        animation.finished.connect(lambda b=button, r=base_rect: b.setGeometry(r))

        self._button_animations[button] = animation
        animation.start()

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

    def get_last_foreground_hwnd(self) -> int | None:
        return self._last_foreground_hwnd

    def reset_current_word(self) -> None:
        self._current_word = ""
        self._preview_label.setText("")

    def _on_button_clicked(self, value: str, button: QPushButton) -> None:
        self._animate_press(button)
        self._trigger_ripple(button)

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

    def _trigger_ripple(self, button: QPushButton) -> None:
        if not getattr(self, "_ripple_overlay", None):
            return

        center = QPoint(int(self.width() / 2), int(self.height() / 2))
        target = QPoint(
            button.x() + button.width() // 2,
            button.y() + button.height() // 2,
        )

        accent_color = QColor("#fab387")
        self._ripple_overlay.trigger(center, target, accent_color)

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
                suggestions = _sym_spell.lookup(word, Verbosity.CLOSEST, max_edit_distance=2)
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


class MicrophonePanel(QWidget):
    """Pestaña de micrófono: botón de dictado + selector de dispositivo."""

    def __init__(self, keyboard_panel: CircularButtonPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keyboard_panel = keyboard_panel
        self._is_listening = False
        self._selected_device_index: int | None = None

        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(24)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Dictado por voz")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color: #cdd6f4;"
            "font-size: 18px;"
            "font-weight: 600;"
            "background: transparent;"
        )
        layout.addWidget(title)

        self._mic_button = QPushButton("🎤")
        self._mic_button.setFixedSize(QSize(110, 110))
        self._mic_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mic_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._set_mic_idle_style()
        self._mic_button.clicked.connect(self._toggle_listening)

        mic_row = QHBoxLayout()
        mic_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mic_row.addWidget(self._mic_button)
        layout.addLayout(mic_row)

        self._status_label = QLabel("Listo. Pulsa el micrófono y habla.")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            "color: #a6adc8;"
            "font-size: 13px;"
            "background: transparent;"
        )
        layout.addWidget(self._status_label)

        device_label = QLabel("Dispositivo de entrada")
        device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        device_label.setStyleSheet(
            "color: #a6adc8;"
            "font-size: 12px;"
            "font-weight: 600;"
            "background: transparent;"
        )
        layout.addWidget(device_label)

        self._device_combo = QComboBox()
        self._device_combo.setFixedWidth(320)
        self._device_combo.setStyleSheet(
            "QComboBox {"
            "background-color: #313244;"
            "color: #cdd6f4;"
            "border: 2px solid #585b70;"
            "border-radius: 6px;"
            "padding: 6px 10px;"
            "font-size: 13px;"
            "}"
            "QComboBox::drop-down {"
            "border: none;"
            "}"
            "QComboBox QAbstractItemView {"
            "background-color: #313244;"
            "color: #cdd6f4;"
            "selection-background-color: #45475a;"
            "}"
        )
        combo_row = QHBoxLayout()
        combo_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        combo_row.addWidget(self._device_combo)
        layout.addLayout(combo_row)

        refresh_button = QPushButton("Actualizar lista")
        refresh_button.setFixedWidth(160)
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        refresh_button.setStyleSheet(
            "QPushButton {"
            "background-color: #45475a;"
            "color: #cdd6f4;"
            "border: 2px solid #585b70;"
            "border-radius: 6px;"
            "font-size: 12px;"
            "font-weight: 600;"
            "padding: 6px;"
            "}"
            "QPushButton:hover {"
            "background-color: #585b70;"
            "}"
        )
        refresh_button.clicked.connect(self._populate_devices)
        refresh_row = QHBoxLayout()
        refresh_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        refresh_row.addWidget(refresh_button)
        layout.addLayout(refresh_row)

        self._device_combo.currentIndexChanged.connect(self._on_device_changed)

        self._populate_devices()

    # === Estilos del botón de micrófono ===
    def _set_mic_idle_style(self) -> None:
        self._mic_button.setStyleSheet(
            "QPushButton {"
            "background-color: #313244;"
            "color: #94e2d5;"
            "border: 3px solid #94e2d5;"
            "border-radius: 55px;"
            "font-size: 36px;"
            "}"
            "QPushButton:hover {"
            "background-color: #45475a;"
            "}"
        )

    def _set_mic_listening_style(self) -> None:
        self._mic_button.setStyleSheet(
            "QPushButton {"
            "background-color: #94e2d5;"
            "color: #11111b;"
            "border: 3px solid #94e2d5;"
            "border-radius: 55px;"
            "font-size: 36px;"
            "}"
        )

    def _set_mic_error_style(self) -> None:
        self._mic_button.setStyleSheet(
            "QPushButton {"
            "background-color: #313244;"
            "color: #f38ba8;"
            "border: 3px solid #f38ba8;"
            "border-radius: 55px;"
            "font-size: 36px;"
            "}"
            "QPushButton:hover {"
            "background-color: #45475a;"
            "}"
        )

    # === Listado de dispositivos ===
    def _populate_devices(self) -> None:
        self._device_combo.clear()

        if sr is None:
            self._device_combo.addItem("speech_recognition no instalado")
            self._device_combo.setEnabled(False)
            self._status_label.setText(
                "Falta instalar dependencias: pip install SpeechRecognition pyaudio"
            )
            self._set_mic_error_style()
            return

        try:
            names = sr.Microphone.list_microphone_names()
            _mic_log(f"Dispositivos detectados: {names}")
        except Exception as exc:
            _mic_log(f"Error listando dispositivos: {exc}\n{traceback.format_exc()}")
            self._device_combo.addItem("Error al listar dispositivos")
            self._device_combo.setEnabled(False)
            self._status_label.setText(f"Error al listar dispositivos: {exc}")
            self._set_mic_error_style()
            return

        if not names:
            self._device_combo.addItem("No se detectaron micrófonos")
            self._device_combo.setEnabled(False)
            self._status_label.setText("No se detectó ningún micrófono.")
            self._set_mic_error_style()
            return

        self._device_combo.setEnabled(True)
        for index, name in enumerate(names):
            self._device_combo.addItem(f"{index}: {name}", userData=index)

        self._device_combo.setCurrentIndex(0)
        self._selected_device_index = self._device_combo.itemData(0)
        self._status_label.setText("Listo. Pulsa el micrófono y habla.")
        self._set_mic_idle_style()

    def _on_device_changed(self, index: int) -> None:
        if index < 0:
            return
        self._selected_device_index = self._device_combo.itemData(index)
        _mic_log(f"Dispositivo seleccionado: {self._selected_device_index}")

    # === Dictado ===
    def _toggle_listening(self) -> None:
        if sr is None:
            self._status_label.setText(
                "Falta instalar dependencias: pip install SpeechRecognition pyaudio"
            )
            self._set_mic_error_style()
            return

        if self._is_listening:
            _mic_log("Ya hay una escucha en curso, se ignora la pulsación.")
            return

        self._is_listening = True
        self._set_mic_listening_style()
        self._status_label.setText("Escuchando... habla ahora.")
        _mic_log("Iniciando hilo de escucha.")

        thread = threading.Thread(target=self._listen_and_transcribe, daemon=True)
        thread.start()

    def _listen_and_transcribe(self) -> None:
        recognizer = sr.Recognizer()

        try:
            _mic_log(f"Abriendo Microphone(device_index={self._selected_device_index})")
            with sr.Microphone(device_index=self._selected_device_index) as source:
                _mic_log("Microphone abierto. Ajustando ruido ambiente...")
                recognizer.adjust_for_ambient_noise(source, duration=0.25)
                _mic_log("Ruido ambiente ajustado. Escuchando audio...")
                audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
                _mic_log("Audio capturado. Enviando a Google Speech API...")
                recognizer.pause_threshold = 0.5

            text = recognizer.recognize_google(audio, language="es-ES")
            _mic_log(f"Texto reconocido: '{text}'")

            if text:
                target_hwnd = self._keyboard_panel.get_last_foreground_hwnd()
                _mic_log(f"target_hwnd={target_hwnd}")

                if target_hwnd and win32gui.IsWindow(target_hwnd):
                    _send_text(text + " ")
                    self._keyboard_panel.reset_current_word()
                    QTimer.singleShot(0, lambda: self._status_label.setText(f"Escrito: \"{text}\""))
                else:
                    _mic_log(
                        "No hay ventana destino válida. Pasa el ratón sobre la "
                        "ventana de texto donde quieres escribir antes de dictar."
                    )
                    QTimer.singleShot(
                        0,
                        lambda: self._status_label.setText(
                            "No hay ventana destino. Pasa el ratón sobre la app "
                            "donde quieres escribir antes de dictar."
                        ),
                    )

        except sr.WaitTimeoutError:
            _mic_log("Timeout: no se detectó voz a tiempo.")
            QTimer.singleShot(0, lambda: self._status_label.setText("No se detectó voz. Intenta de nuevo."))
        except sr.UnknownValueError:
            _mic_log("No se entendió el audio.")
            QTimer.singleShot(0, lambda: self._status_label.setText("No se entendió el audio. Intenta de nuevo."))
        except sr.RequestError as exc:
            _mic_log(f"Error de servicio de reconocimiento: {exc}")
            QTimer.singleShot(0, lambda e=exc: self._status_label.setText(f"Error de red/servicio: {e}"))
        except OSError as exc:
            _mic_log(f"OSError abriendo el micrófono: {exc}\n{traceback.format_exc()}")
            QTimer.singleShot(
                0,
                lambda e=exc: self._status_label.setText(
                    f"No se pudo abrir el micrófono ({e}). Prueba otro dispositivo."
                ),
            )
        except Exception as exc:
            _mic_log(f"Error inesperado: {exc}\n{traceback.format_exc()}")
            QTimer.singleShot(0, lambda e=exc: self._status_label.setText(f"Error inesperado: {e}"))
        finally:
            self._is_listening = False
            QTimer.singleShot(0, self._set_mic_idle_style)


class TabBar(QWidget):
    """Barra superior con dos botones tipo pestaña: Teclado / Micrófono."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 0)
        layout.setSpacing(8)

        self.keyboard_button = QPushButton("Teclado")
        self.mic_button = QPushButton("Micrófono")

        for button in (self.keyboard_button, self.mic_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCheckable(True)
            button.setFixedHeight(32)

        layout.addWidget(self.keyboard_button)
        layout.addWidget(self.mic_button)

        self.keyboard_button.setChecked(True)
        self._update_styles()

        self.keyboard_button.clicked.connect(lambda: self._select(0))
        self.mic_button.clicked.connect(lambda: self._select(1))

        self.on_tab_changed = None  # callback(index: int)

    def _select(self, index: int) -> None:
        self.keyboard_button.setChecked(index == 0)
        self.mic_button.setChecked(index == 1)
        self._update_styles()
        if self.on_tab_changed:
            self.on_tab_changed(index)

    def _update_styles(self) -> None:
        active_style = (
            "QPushButton {"
            "background-color: #fab387;"
            "color: #11111b;"
            "border: 2px solid #fab387;"
            "border-radius: 8px;"
            "font-size: 13px;"
            "font-weight: 600;"
            "}"
        )
        inactive_style = (
            "QPushButton {"
            "background-color: #313244;"
            "color: #cdd6f4;"
            "border: 2px solid #585b70;"
            "border-radius: 8px;"
            "font-size: 13px;"
            "font-weight: 500;"
            "}"
            "QPushButton:hover {"
            "background-color: #45475a;"
            "}"
        )

        self.keyboard_button.setStyleSheet(
            active_style if self.keyboard_button.isChecked() else inactive_style
        )
        self.mic_button.setStyleSheet(
            active_style if self.mic_button.isChecked() else inactive_style
        )


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
        self.setWindowOpacity(0.95)

        central_widget = QWidget(self)
        central_widget.setObjectName("central_widget")
        central_widget.setStyleSheet(
            "#central_widget {"
            "background-color: #1e1e2e;"
            "border: 1px solid #313244;"
            "border-radius: 30px;"
            "}"
        )

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._tab_bar = TabBar(central_widget)
        outer_layout.addWidget(self._tab_bar)

        self._stack = QStackedWidget(central_widget)
        self._stack.setStyleSheet("background: transparent;")
        outer_layout.addWidget(self._stack)

        self._button_panel = CircularButtonPanel(
            ring_letters=self._CONSONANTS,
            center_letters=self._VOWELS,
            button_diameter=CircularButtonPanel.BUTTON_SIZE,
        )
        self._microphone_panel = MicrophonePanel(self._button_panel)

        self._stack.addWidget(self._button_panel)
        self._stack.addWidget(self._microphone_panel)

        self._tab_bar.on_tab_changed = self._stack.setCurrentIndex

        central_widget.resize(self.size())
        self.setCentralWidget(central_widget)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if central := self.centralWidget():
            central.resize(self.size())

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