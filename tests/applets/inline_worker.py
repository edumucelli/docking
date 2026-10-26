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
