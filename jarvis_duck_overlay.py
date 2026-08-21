import sys
from PyQt6.QtWidgets import QApplication
from patito_jar_overlay import PatitoJarOverlay

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = PatitoJarOverlay(backend_url="http://127.0.0.1:8000")
    window.show()
    window.raise_()
    window.activateWindow()
    sys.exit(app.exec())
