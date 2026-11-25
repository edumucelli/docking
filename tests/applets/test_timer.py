from unittest.mock import MagicMock

from docking.applets import timer


def test_one_shot_replaces_pending_source_and_clears_before_callback(monkeypatch):
    add = MagicMock(side_effect=[11, 12, 13])
    remove = MagicMock()
    monkeypatch.setattr(timer.GLib, "timeout_add_seconds", add)
    monkeypatch.setattr(timer.GLib, "source_remove", remove)
    source = timer.OneShotTimer()

    source.schedule(1, lambda: None)
    source.schedule(2, lambda: source.schedule(1, lambda: None))
    remove.assert_called_once_with(11)
    assert add.call_args.args[0] == 2
    assert add.call_args.args[1]() is False
    remove.assert_called_once_with(11)

    source.cancel()
    source.cancel()
    assert [args[0] for args, _ in remove.call_args_list] == [11, 13]
