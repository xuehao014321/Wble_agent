import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow


class OnboardingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_stdout = sys.stdout
        self.original_stderr = sys.stderr
        self.window = MainWindow()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr
        self.window.onboarding_checked = True
        self.window.show()
        QTest.qWait(80)

    def tearDown(self):
        self.window.spotlight_guide.hide()
        self.window.is_quitting = True
        self.window.close()
        sys.stdout = self.original_stdout
        sys.stderr = self.original_stderr

    def test_spotlight_guide_can_reach_every_step(self):
        guide = self.window.spotlight_guide
        self.assertEqual(len(guide.steps), 8)
        guide.start()

        for index in range(len(guide.steps)):
            guide.step_index = index
            guide.show_step()
            QTest.qWait(100)
            self.assertTrue(
                guide.highlight_rect.isValid(),
                f"Step {index + 1} has no valid highlight",
            )
            target = guide.steps[index]["target"]
            target_center = guide.mapFromGlobal(
                target.mapToGlobal(target.rect().center())
            )
            self.assertTrue(
                guide.highlight_rect.contains(target_center),
                f"Step {index + 1} highlight missed its target",
            )

        self.assertEqual(guide.next_button.text(), "完成")


if __name__ == "__main__":
    unittest.main()
