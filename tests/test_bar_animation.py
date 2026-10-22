# pyright: reportPrivateUsage=false

import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, patch

from PyQt6.QtCore import QByteArray, QRect, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QWidget

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.bar_helper import BarAnimationManager  # noqa: E402

if TYPE_CHECKING:
    from core.bar import Bar


class AnimationBar(QWidget):
    animation_tick = pyqtSignal()
    animation_finished = pyqtSignal()
    opacity_tick = pyqtSignal(float)

    def __init__(self):
        super().__init__()
        self.animation = {"enabled": True, "type": "slide", "duration": 1000}
        self.alignment = {"position": "top"}
        self.autohide_manager = None
        self.skip_animation = False
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
        bar = AnimationBar()
        ticks: list[QRect] = []
        bar.animation_tick.connect(lambda: ticks.append(bar.geometry()))
        manager = BarAnimationManager(cast("Bar", bar))

        with patch.object(manager, "_slide_is_blocked", return_value=False):
            manager._start_slide(show=True)
        animation = manager._animation
        if animation is None:
            self.fail("slide animation was not created")
        animation.setCurrentTime(100)
        self.app.processEvents()

        self.assertEqual((bar.width(), bar.height()), (300, 40))
        self.assertNotEqual(bar.pos().y(), 20)
        self.assertGreater(len(ticks), 0)

        manager.cleanup()
        bar.close()

    def test_slide_falls_back_to_fade_when_adjacent_screen_blocks_it(self):
        bar = AnimationBar()
        opacity_ticks: list[float] = []
        bar.opacity_tick.connect(opacity_ticks.append)
        manager = BarAnimationManager(cast("Bar", bar))

        with patch.object(manager, "_slide_is_blocked", return_value=True):
            manager._start_slide(show=True)
        animation = manager._animation
        if animation is None:
            self.fail("fade animation was not created")
        animation.setCurrentTime(100)
        self.app.processEvents()

        self.assertEqual(animation.propertyName(), QByteArray(b"windowOpacity"))
        self.assertGreater(len(opacity_ticks), 0)

        manager.cleanup()
        bar.close()

    def test_hide_finish_repositions_native_window_and_notifies_overlays(self):
        bar = Mock()
        window = Mock()
        bar.windowHandle.return_value = window
        manager = BarAnimationManager(cast("Bar", bar))
        manager._target_geo = (100, 20, 300, 40)

        manager._on_hide_finished()

        window.setPosition.assert_called_once_with(100, 20)
        bar.animation_finished.emit.assert_called_once_with()
        self.assertFalse(bar.skip_animation)


if __name__ == "__main__":
    unittest.main()
