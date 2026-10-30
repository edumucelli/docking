"""Optional native artwork stays behind a lazy, binding-free source contract."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

from docking.platform import environment, icon_source, icons
from docking.platform.backends.windows import icons as windows_icons
from tests.platform.application_fakes import application


def test_no_optional_source_keeps_gio_fallback_and_selects_only_once(monkeypatch):
    select = Mock(return_value=None)
    monkeypatch.setattr(icons, "create_application_icon_source", select)
    loader = icons.IconLoader()
    loader.stop()
    select.assert_not_called()
    retained = object()
    monkeypatch.setattr(loader, "load_gicon", lambda **_kwargs: retained)
    for identifier in ("One.App", "Two.App"):
        assert (
            loader.load_desktop_icon(application(identifier), 16, gicon=object())
            is retained
        )
    select.assert_called_once()
    loader.stop()


def test_windows_factory_passes_readiness_to_its_provider(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    ready, provider = Mock(), Mock()
    create = Mock(return_value=provider)
    monkeypatch.setattr(windows_icons, "WindowsIconCache", create)
    assert icon_source.create_application_icon_source(ready) is provider
    create.assert_called_once_with(ready)


def test_unix_factory_never_imports_gtk_or_windows_implementation():
    root = Path(__file__).resolve().parents[2]
    script = (
        "import sys,json;sys.path.insert(0,sys.argv[1]);"
        "from docking.platform import icon_source,environment;"
        "environment.is_windows=lambda:False;"
        "assert icon_source.create_application_icon_source(lambda identifier:None) is None;"
        "print(json.dumps([name for name in sys.modules if name=='gi' "
        "or name.startswith('docking.platform.backends.windows') "
        "or name=='docking.platform.icons' "
        "or name.startswith('docking.platform.applications')]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(root)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == []
