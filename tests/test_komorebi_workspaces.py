import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.validation.widgets.komorebi.workspaces import KomorebiWorkspacesConfig  # noqa: E402
from core.widgets.komorebi.workspaces import (  # noqa: E402
    WorkspaceWidget,
    _format_workspace_labels,
    _should_hide_workspace_label,
    _workspace_topology_signature,
)


class WorkspaceLabelTests(unittest.TestCase):
    def test_uses_komorebi_name_when_present(self):
        labels = _format_workspace_labels("research", 3, 2, "{index}", "{name}", "[{name}]", "{name}*")

        self.assertEqual(labels, ("research", "[research]", "research*"))

    def test_uses_index_when_workspace_name_and_configured_default_are_empty(self):
        labels = _format_workspace_labels(None, 3, 2, "", "{name}", "{name}", "{name}")

        self.assertEqual(labels, ("3", "3", "3"))

    def test_preserves_explicit_whitespace_labels(self):
        labels = _format_workspace_labels(None, 3, 2, "{index}", "    ", "", "  ")

        self.assertEqual(labels, ("    ", "", "  "))

    def test_validation_default_falls_back_to_index(self):
        self.assertEqual(KomorebiWorkspacesConfig().label_default_name, "{index}")


class WorkspaceIconLabelTests(unittest.TestCase):
    def test_hides_label_only_when_enabled_and_icons_exist(self):
        self.assertTrue(_should_hide_workspace_label(True, True))
        self.assertFalse(_should_hide_workspace_label(True, False))
        self.assertFalse(_should_hide_workspace_label(False, True))


class WorkspaceTopologyTests(unittest.TestCase):
    def test_signature_tracks_monitor_workspace_count_and_names(self):
        screen = {"id": 131224}
        original = _workspace_topology_signature(screen, [{"index": 0, "name": None}])
        renamed = _workspace_topology_signature(screen, [{"index": 0, "name": "1"}])
        expanded = _workspace_topology_signature(
            screen,
            [{"index": 0, "name": "1"}, {"index": 1, "name": "2"}],
        )

        self.assertNotEqual(original, renamed)
        self.assertNotEqual(renamed, expanded)

    def test_reconcile_hides_buttons_removed_from_monitor(self):
        class Button:
            def __init__(self):
                self.hidden = False

            def hide(self):
                self.hidden = True

        class Widget:
            _komorebi_workspaces = [{"index": 0}]
            _workspace_buttons = [Button(), Button(), Button()]
            updated = False

            def _add_or_update_buttons(self):
                self.updated = True

        widget = Widget()
        WorkspaceWidget._reconcile_workspace_buttons(widget)

        self.assertFalse(widget._workspace_buttons[0].hidden)
        self.assertTrue(widget._workspace_buttons[1].hidden)
        self.assertTrue(widget._workspace_buttons[2].hidden)
        self.assertTrue(widget.updated)


if __name__ == "__main__":
    unittest.main()
