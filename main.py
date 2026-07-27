import sys
import asyncio
from PyQt6.QtWidgets import QApplication
import qasync

from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    # Use qasync event loop to bridge PyQt6 and asyncio (Playwright)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    
    window = MainWindow()
    window.show()
    
    with loop:
        loop.run_forever()

if __name__ == "__main__":
    main()
