"""Execute ordinary UI test requests inline without retaining an IO thread."""

from types import SimpleNamespace

from docking.applets.worker import BackgroundWorker


class InlineSerialWorker(BackgroundWorker):
    def __init__(self, *, on_close, **kwargs):
        super().__init__(
            thread_factory=lambda target, daemon: SimpleNamespace(start=target),
            idle_add=lambda callback, *args: callback(*args),
            **kwargs,
        )
        self._on_close = on_close

    def stop(self):
        self._on_close()


class ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)
