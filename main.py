"""Launch the focus-navigation MVP shell."""

import sys

from PyQt6.QtWidgets import QApplication

from presenter_focus import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())