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

# ---------------------------------------------------------------------------
# Fake GTK widgets for testing UI construction
# ---------------------------------------------------------------------------


class _FakeWidget:
    def __init__(self) -> None:
        self.children: list[_FakeWidget] = []
        self.pack_start_calls: list[tuple] = []
        self.border_width = 0
        self.label = ""
        self.markup = ""
        self.xalign = 0.0
        self.editable = True
        self.cursor_visible = True
        self.monospace = False
        self.wrap_mode = None
        self.selectable = False
        self.max_width_chars = 0
        self.ellipsize = None
        self.line_wrap = False
        self.modal = False
        self.resizable = True
        self.default_size = (0, 0)
        self.position = None
        self.destroyed = False
        self.title = ""
        self.decorated = True
        self.spacing = 0
        self.layout = None
        self._buffer_text = ""
        self._text_buffer = None
        self._size_request = (-1, -1)

    def set_border_width(self, w: int) -> None:
        self.border_width = w

    def set_xalign(self, v: float) -> None:
        self.xalign = v

    def set_markup(self, m: str) -> None:
        self.markup = m

    def set_editable(self, v: bool) -> None:
        self.editable = v

    def set_cursor_visible(self, v: bool) -> None:
        self.cursor_visible = v

    def set_monospace(self, v: bool) -> None:
        self.monospace = v

    def set_wrap_mode(self, v: object) -> None:
        self.wrap_mode = v

    def set_selectable(self, v: bool) -> None:
        self.selectable = v

    def set_max_width_chars(self, v: int) -> None:
        self.max_width_chars = v

    def set_ellipsize(self, v: object) -> None:
        self.ellipsize = v

    def set_line_wrap(self, v: bool) -> None:
        self.line_wrap = v

    def set_default_size(self, w: int, h: int) -> None:
        self.default_size = (w, h)

    def set_position(self, v: object) -> None:
        self.position = v

    def set_modal(self, v: bool) -> None:
        self.modal = v

    def set_resizable(self, v: bool) -> None:
        self.resizable = v

    def set_decorated(self, v: bool) -> None:
        self.decorated = v

    def set_title(self, t: str) -> None:
        self.title = t

    def set_layout(self, v: object) -> None:
        self.layout = v

    def set_spacing(self, v: int) -> None:
        self.spacing = v

    def set_size_request(self, w: int, h: int) -> None:
        self._size_request = (w, h)

    def show_all(self) -> None:
        pass

    def present(self) -> None:
        pass

    def destroy(self) -> None:
        self.destroyed = True

    def connect(self, signal: str, callback: object) -> None:
        pass

    def get_style_context(self) -> _FakeWidget:
        return self

    def add_class(self, name: str) -> None:
        pass

    def add(self, child: _FakeWidget) -> None:
        self.children.append(child)

    def pack_start(
        self, child: _FakeWidget, expand: bool, fill: bool, padding: int
    ) -> None:
        self.children.append(child)
        self.pack_start_calls.append((child, expand, fill, padding))

    def get_buffer(self) -> _FakeWidget:
        if self._text_buffer is None:
            self._text_buffer = _FakeWidget()
        return self._text_buffer

    def set_text(self, text: str) -> None:
        self._buffer_text = text


class _FakeGrid(_FakeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.column_spacing = 0
        self.row_spacing = 0
        self.attached: list[tuple] = []

    def set_column_spacing(self, v: int) -> None:
        self.column_spacing = v

    def set_row_spacing(self, v: int) -> None:
        self.row_spacing = v

    def attach(self, child: _FakeWidget, col: int, row: int, w: int, h: int) -> None:
        self.attached.append((child, col, row, w, h))


class _FakeNotebook(_FakeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.pages: list[tuple[_FakeWidget, _FakeWidget]] = []

    def append_page(self, child: _FakeWidget, tab_label: _FakeWidget) -> None:
        self.pages.append((child, tab_label))


class _FakeScrolledWindow(_FakeWidget):
    def set_policy(self, h: object, v: object) -> None:
        pass


class _FakeFileChooser(_FakeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.current_name = ""
        self.overwrite_confirmation = False
        self._filename = ""
        self._response = 1  # OK by default

    def set_current_name(self, name: str) -> None:
        self.current_name = name

    def set_do_overwrite_confirmation(self, v: bool) -> None:
        self.overwrite_confirmation = v

    def run(self) -> int:
        return self._response

    def get_filename(self) -> str | None:
        return self._filename or None


class _FakeMessageDialog(_FakeWidget):
    def run(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(**overrides):
    """Build a minimal fake DiagnosticsSnapshot."""
    from dataclasses import replace
    from datetime import datetime, timezone

    from docking.platform.backends.base import DisplayServer
    from docking.platform.diagnostics import (
        DiagnosticsSnapshot,
        MonitorDiagnostic,
    )

    base = DiagnosticsSnapshot(
        generated_at=datetime.now(timezone.utc),
        docking_version="2.4.1",
        os_name="TestOS",
        desktop="GNOME",
        session_type="wayland",
        backend_name="test-backend",
        backend_class="TestBackend",
        display_server=DisplayServer.WAYLAND,
        gtk_backend="Wayland",
        gtk_version="3.24",
        python_version="3.11",
        forced_backend=None,
        xwayland=False,
        wayland_session=True,
        x11_backend=False,
        compositor_active=True,
        features=(),
        checks=(),
        monitors=(
            MonitorDiagnostic(
                index=0,
                name="DP-1",
                primary=True,
                geometry="1920x1080+0+0",
                scale=1,
            ),
        ),
        environment={"XDG_SESSION_TYPE": "wayland"},
    )
    if overrides:
        return replace(base, **overrides)
    return base


def _fake_window_and_backend():
    parent = MagicMock()
    backend = MagicMock()
    parent.get_display.return_value = MagicMock()
    return parent, backend


# ---------------------------------------------------------------------------
# Static methods
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


class TestDiagnosticsControllerInit:
    def test_init_sets_parent_and_backend(self):
        parent = MagicMock()
        backend = MagicMock()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        assert controller._parent is parent
        assert controller._backend is backend
        assert controller._window is None
        assert controller._snapshot is None


# ---------------------------------------------------------------------------
# Show
# ---------------------------------------------------------------------------


class TestDiagnosticsShow:
    def test_show_destroys_previous_window(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)

        snapshot = _make_snapshot()
        monkeypatch.setattr(mod, "collect_diagnostics", lambda **kw: snapshot)

        old_window = MagicMock()
        controller._window = old_window

        # Replace _build_window to return a fake
        new_window = MagicMock()
        controller._build_window = MagicMock(return_value=new_window)

        controller.show()

        old_window.destroy.assert_called_once()
        controller._build_window.assert_called_once_with(snapshot)
        new_window.show_all.assert_called_once()
        new_window.present.assert_called_once()
        assert controller._snapshot is snapshot

    def test_show_without_previous_window(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)

        snapshot = _make_snapshot()
        monkeypatch.setattr(mod, "collect_diagnostics", lambda **kw: snapshot)

        new_window = MagicMock()
        controller._build_window = MagicMock(return_value=new_window)

        controller.show()

        new_window.show_all.assert_called_once()
        assert controller._snapshot is snapshot


# ---------------------------------------------------------------------------
# On destroy
# ---------------------------------------------------------------------------


class TestDiagnosticsOnDestroy:
    def test_on_destroy_clears_window(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        fake_window = MagicMock()
        controller._window = fake_window
        controller._on_destroy(fake_window)
        assert controller._window is None

    def test_on_destroy_different_window_ignored(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        original = MagicMock()
        controller._window = original
        controller._on_destroy(MagicMock())
        assert controller._window is original


# ---------------------------------------------------------------------------
# Current report
# ---------------------------------------------------------------------------


class TestDiagnosticsCurrentReport:
    def test_current_report_uses_existing_snapshot(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
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

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        fake_snapshot = MagicMock()
        monkeypatch.setattr(mod, "collect_diagnostics", lambda **kw: fake_snapshot)
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "report text")
        report = controller._current_report()
        assert controller._snapshot is fake_snapshot
        assert report == "report text"


# ---------------------------------------------------------------------------
# Feature row
# ---------------------------------------------------------------------------


class TestDiagnosticsFeatureRow:
    def test_feature_row_available(self):
        parent, backend = _fake_window_and_backend()
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
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        feature = DiagnosticFeature(
            id="compositing",
            label="Compositing",
            detail="No compositor detected",
            available=False,
        )
        row = controller._feature_row(feature)
        assert row is not None


# ---------------------------------------------------------------------------
# Check row
# ---------------------------------------------------------------------------


class TestDiagnosticsCheckRow:
    def test_check_row_with_fix_hint(self):
        parent, backend = _fake_window_and_backend()
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
        parent, backend = _fake_window_and_backend()
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


# ---------------------------------------------------------------------------
# Copy report
# ---------------------------------------------------------------------------


class TestDiagnosticsCopyReport:
    def test_on_copy_report_sets_clipboard(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._snapshot = _make_snapshot()
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "my report")

        fake_clipboard = MagicMock()
        monkeypatch.setattr(mod.Gtk, "Clipboard", MagicMock())
        mod.Gtk.Clipboard.get.return_value = fake_clipboard

        controller._on_copy_report()

        mod.Gtk.Clipboard.get.assert_called_once()
        fake_clipboard.set_text.assert_called_once_with("my report", -1)
        fake_clipboard.store.assert_called_once()


# ---------------------------------------------------------------------------
# Save report
# ---------------------------------------------------------------------------


class TestDiagnosticsSaveReport:
    def test_on_save_report_no_window_returns(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._window = None
        # Should not raise
        controller._on_save_report()

    def test_on_save_report_saves_successfully(self, monkeypatch, tmp_path):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._snapshot = _make_snapshot()
        controller._window = MagicMock()
        monkeypatch.setattr(
            mod, "format_diagnostics_report", lambda s: "report content"
        )

        report_path = tmp_path / "docking-diagnostics.md"

        fake_chooser = MagicMock()
        fake_chooser.run.return_value = mod.Gtk.ResponseType.ACCEPT
        fake_chooser.get_filename.return_value = str(report_path)
        monkeypatch.setattr(mod.Gtk, "FileChooserNative", lambda **kw: fake_chooser)

        controller._on_save_report()

        assert report_path.exists()
        assert report_path.read_text() == "report content"

    def test_on_save_report_user_cancels(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._window = MagicMock()
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "x")

        fake_chooser = MagicMock()
        fake_chooser.run.return_value = mod.Gtk.ResponseType.CANCEL
        monkeypatch.setattr(mod.Gtk, "FileChooserNative", lambda **kw: fake_chooser)

        controller._on_save_report()
        # Should not raise

    def test_on_save_report_no_filename(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._window = MagicMock()

        fake_chooser = MagicMock()
        fake_chooser.run.return_value = mod.Gtk.ResponseType.ACCEPT
        fake_chooser.get_filename.return_value = None
        monkeypatch.setattr(mod.Gtk, "FileChooserNative", lambda **kw: fake_chooser)

        controller._on_save_report()
        # Should not raise

    def test_on_save_report_os_error_shows_error_dialog(self, monkeypatch, tmp_path):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._snapshot = _make_snapshot()
        controller._window = MagicMock()
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "x")

        # Path to a directory so write fails
        fake_chooser = MagicMock()
        fake_chooser.run.return_value = mod.Gtk.ResponseType.ACCEPT
        fake_chooser.get_filename.return_value = str(tmp_path)  # directory, not file
        monkeypatch.setattr(mod.Gtk, "FileChooserNative", lambda **kw: fake_chooser)

        error_calls: list[str] = []
        monkeypatch.setattr(
            controller, "_show_error", lambda msg: error_calls.append(msg)
        )

        controller._on_save_report()

        assert len(error_calls) == 1


# ---------------------------------------------------------------------------
# Show error
# ---------------------------------------------------------------------------


class TestDiagnosticsShowError:
    def test_show_error_no_window_returns(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._window = None
        # Should not raise
        controller._show_error("test error")

    def test_show_error_creates_dialog(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        controller._window = MagicMock()

        fake_dialog = MagicMock()
        monkeypatch.setattr(mod.Gtk, "MessageDialog", lambda **kw: fake_dialog)

        controller._show_error("test error message")

        fake_dialog.run.assert_called_once()
        fake_dialog.destroy.assert_called_once()


# ---------------------------------------------------------------------------
# New section header / kv grid helpers
# ---------------------------------------------------------------------------


class TestDiagnosticsHelpers:
    def test_new_section_header_creates_markup_label(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        label = controller._new_section_header("Test Section")
        assert label is not None

    def test_new_kv_grid(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        grid = controller._new_kv_grid()
        assert grid is not None

    def test_append_kv_row(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        grid = controller._new_kv_grid()
        controller._append_kv_row(grid, 0, "Key", "Value")
        # Should not raise


# ---------------------------------------------------------------------------
# Tab builders
# ---------------------------------------------------------------------------


class TestDiagnosticsTabBuilders:
    def test_build_overview_tab_without_warnings(self, monkeypatch):

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        snapshot = _make_snapshot(checks=())

        tab = controller._build_overview_tab(snapshot)
        assert tab is not None

    def test_build_overview_tab_with_warnings(self, monkeypatch):

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)

        snapshot = _make_snapshot(
            checks=(
                DiagnosticCheck(
                    id="w1",
                    label="Warning 1",
                    detail="Detail 1",
                    status="warning",
                    fix_hint=None,
                ),
            ),
        )

        tab = controller._build_overview_tab(snapshot)
        assert tab is not None

    def test_build_features_tab(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)

        snapshot = _make_snapshot(
            features=(
                DiagnosticFeature(
                    id="f1",
                    label="Feature 1",
                    detail="Detail",
                    available=True,
                ),
                DiagnosticFeature(
                    id="f2",
                    label="Feature 2",
                    detail="Detail",
                    available=False,
                ),
            ),
        )

        tab = controller._build_features_tab(snapshot)
        assert tab is not None

    def test_build_checks_tab(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)

        snapshot = _make_snapshot(
            checks=(
                DiagnosticCheck(
                    id="c1",
                    label="Check 1",
                    detail="Detail",
                    status="ok",
                    fix_hint=None,
                ),
                DiagnosticCheck(
                    id="c2",
                    label="Check 2",
                    detail="Detail",
                    status="error",
                    fix_hint="Fix me",
                ),
            ),
        )

        tab = controller._build_checks_tab(snapshot)
        assert tab is not None

    def test_build_environment_tab(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        snapshot = _make_snapshot()

        tab = controller._build_environment_tab(snapshot)
        assert tab is not None

    def test_build_environment_tab_no_monitors(self):
        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        snapshot = _make_snapshot(monitors=())

        tab = controller._build_environment_tab(snapshot)
        assert tab is not None

    def test_build_report_tab(self, monkeypatch):
        import docking.ui.diagnostics as mod

        parent, backend = _fake_window_and_backend()
        controller = DiagnosticsDialogController(parent=parent, backend=backend)
        snapshot = _make_snapshot()
        monkeypatch.setattr(mod, "format_diagnostics_report", lambda s: "report text")

        tab = controller._build_report_tab(snapshot)
        assert tab is not None
