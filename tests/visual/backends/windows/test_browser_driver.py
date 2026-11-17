"""Browser fixture helpers preserve native dispatch and response failures."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.visual.backends.windows import browser_driver as mod


def test_browser_script_dispatches_to_gtk_and_decodes_the_callback():
    dispatched = []

    def execute(source, completed):
        assert dispatched == ["gtk"]
        assert source == "window.fixture.storage"
        completed('{"text":"Résumé 日本語","stored":true}')

    def call(callback):
        dispatched.append("gtk")
        return callback()

    browser = SimpleNamespace(_engine=SimpleNamespace(execute=execute))
    assert mod.browser_script(browser, call, "window.fixture.storage") == {
        "text": "Résumé 日本語",
        "stored": True,
    }


def test_browser_script_keeps_the_twenty_second_callback_timeout(monkeypatch):
    done = Mock()
    done.wait.return_value = False
    monkeypatch.setattr(mod.threading, "Event", lambda: done)
    browser = SimpleNamespace(_engine=SimpleNamespace(execute=Mock()))
    with pytest.raises(AssertionError, match="Browser script timed out"):
        mod.browser_script(browser, lambda callback: callback(), "true")
    done.wait.assert_called_once_with(20)


def test_browser_script_preserves_native_execution_errors():
    error = OSError("Browser unavailable")
    browser = SimpleNamespace(_engine=SimpleNamespace(execute=Mock(side_effect=error)))
    with pytest.raises(OSError, match="Browser unavailable"):
        mod.browser_script(browser, lambda callback: callback(), "true")


@pytest.mark.parametrize("ok", [True, False])
def test_worker_request_quotes_inputs_polls_the_reply_and_preserves_failure(ok):
    title = 'Résumé "quoted"\n日本語'
    answer = {"ok": ok, "rows": [title], "error": "Worker refused"}
    script = Mock(side_effect=[True, answer])

    def wait_for(predicate, message, seconds):
        assert message == "No worker reply" and seconds == 25
        return predicate()

    if ok:
        assert mod.worker_request(script, wait_for, "show", title) == [title]
    else:
        with pytest.raises(AssertionError, match="Worker refused"):
            mod.worker_request(script, wait_for, "show", title)
    request = script.call_args_list[0].args[0]
    assert (
        "workerRequest(" + json.dumps("show") + "," + json.dumps({"title": title})
        in request
    )
    assert ".catch(e=>window.fixture.workerReply={ok:false,error:e.message})" in request
    assert script.call_args_list[1].args == ("window.fixture.workerReply",)


def test_worker_request_uses_null_title_for_operations_without_a_title():
    script = Mock(return_value=True)
    wait_for = Mock(return_value={"ok": True, "rows": []})
    assert mod.worker_request(script, wait_for, "close") == []
    assert 'workerRequest("close",{"title": null})' in script.call_args.args[0]


def test_worker_request_does_not_hide_a_reply_timeout():
    wait_for = Mock(side_effect=AssertionError("No worker reply"))
    with pytest.raises(AssertionError, match="No worker reply"):
        mod.worker_request(Mock(), wait_for, "list")
