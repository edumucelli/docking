"""Tests for the diagnostics UI controller.

Focuses on logic that can be tested without a full GTK display.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Ensure GTK mocking happens before the diagnostics module loads
try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    gi_mock.repository.GLib.markup_escape_text.side_effect = lambda text: text
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.diagnostics import DiagnosticCheck, DiagnosticFeature
from docking.ui.diagnostics import DiagnosticsDialogController


class TestDiagnosticsStaticMethods:
    def test_status_symbol_ok(self):
        assert DiagnosticsDialogController._status_symbol("ok") == "OK"

    def test_status_symbol_error(self):
        assert DiagnosticsDialogController._status_symbol("error") == "X"

    def test_status_symbol_warning(self):
        assert DiagnosticsDialogController._status_symbol("warning") == "!"

    def test_status_symbol_unknown(self):
        assert DiagnosticsDialogController._status_symbol("info") == "i"
        assert DiagnosticsDialogController._status_symbol("") == "i"

    def test_yes_no_true(self):
        assert "Yes" in DiagnosticsDialogController._yes_no(True)

    def test_yes_no_false(self):
        assert "No" in DiagnosticsDialogController._yes_no(False)

    def test_unknown_yes_no_with_value(self):
        assert "Yes" in DiagnosticsDialogController._unknown_yes_no(True)
        assert "No" in DiagnosticsDialogController._unknown_yes_no(False)

    def test_unknown_yes_no_with_none(self):
        assert "Unknown" in DiagnosticsDialogController._unknown_yes_no(None)


class TestDiagnosticsControllerInit:
    def test_init_sets_parent_and_backend(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        assert controller._parent is parent
        assert controller._backend is backend
        assert controller._window is None
        assert controller._snapshot is None


class TestDiagnosticsOnDestroy:
    def test_on_destroy_clears_window(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        fake_window = MagicMock()
        controller._window = fake_window
        controller._on_destroy(fake_window)
        assert controller._window is None

    def test_on_destroy_different_window_ignored(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        original = MagicMock()
        controller._window = original
        controller._on_destroy(MagicMock())
        assert controller._window is original


class TestDiagnosticsCurrentReport:
    def test_current_report_uses_existing_snapshot(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        fake_snapshot = MagicMock()
        controller._snapshot = fake_snapshot
        monkeypatch.setattr(
            mod, "format_diagnostics_report", lambda s: "formatted report"
        )
        report = controller._current_report()
        assert report == "formatted report"

    def test_current_report_collects_when_no_snapshot(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent = MagicMock()
        parent.get_display.return_value = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        fake_snapshot = MagicMock()
        monkeypatch.setattr(mod, "collect_diagnostics", lambda **kw: fake_snapshot)
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "report text")
        report = controller._current_report()
        assert controller._snapshot is fake_snapshot
        assert report == "report text"


class TestDiagnosticsFeatureRow:
    def test_feature_row_available(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        feature = DiagnosticFeature(
            id="compositing",
            label="Compositing",
            detail="Compositor is active",
            available=True,
        )
        row = controller._feature_row(feature)
        assert row is not None

    def test_feature_row_unavailable(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        feature = DiagnosticFeature(
            id="compositing",
            label="Compositing",
            detail="No compositor detected",
            available=False,
        )
        row = controller._feature_row(feature)
        assert row is not None


class TestDiagnosticsCheckRow:
    def test_check_row_with_fix_hint(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        check = DiagnosticCheck(
            id="theme",
            label="Theme",
            detail="Missing theme file",
            status="warning",
            fix_hint="Reinstall the theme",
        )
        row = controller._check_row(check)
        assert row is not None

    def test_check_row_without_fix_hint(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        check = DiagnosticCheck(
            id="deps",
            label="Dependencies",
            detail="All dependencies found",
            status="ok",
            fix_hint=None,
        )
        row = controller._check_row(check)
        assert row is not None
