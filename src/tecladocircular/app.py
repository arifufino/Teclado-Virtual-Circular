"""Punto de entrada de la aplicación Circular Keyboard.

Responsabilidad:
- Inicializar configuración y servicios.
- Ejecutar el bucle principal de la aplicación PyQt6.
"""

from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication

from tecladocircular.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
