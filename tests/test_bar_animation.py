import sys
import unittest
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QWidget

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.bar_helper import BarAnimationManager  # noqa: E402


class TestBar(QWidget):
    animation_tick = pyqtSignal()
    animation_finished = pyqtSignal()
    opacity_tick = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self._animation = {"enabled": True, "type": "slide", "duration": 1000}
        self._alignment = {"position": "top"}
        self._autohide_manager = None
        self._skip_animation = False
        self._bar_frame = QFrame(self)
        self.position_bar()
        self._bar_frame.setGeometry(0, 0, self.width(), self.height())

    def position_bar(self):
        self.setGeometry(100, 20, 300, 40)


class BarAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slide_moves_whole_bar_and_notifies_floating_overlays(self):
        bar = TestBar()
        ticks = []
        bar.animation_tick.connect(lambda: ticks.append(bar.geometry()))
        manager = BarAnimationManager(bar)

        manager._start_slide(show=True)
        manager._animation.setCurrentTime(100)
        self.app.processEvents()

        self.assertEqual((bar.width(), bar.height()), (300, 40))
        self.assertNotEqual(bar.pos().y(), 20)
        self.assertGreater(len(ticks), 0)

        manager.cleanup()
        bar.close()


if __name__ == "__main__":
    unittest.main()
