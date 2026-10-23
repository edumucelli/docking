"""Control worker execution and GTK delivery independently, without sleeps."""

from types import SimpleNamespace

from docking.applets.worker import BackgroundWorker


class DeferredSensors:
    def __init__(self):
        self.tasks = []
        self.callbacks = []
        self.worker = BackgroundWorker(
            thread_factory=self._thread,
            idle_add=self._idle_add,
        )

    def _thread(self, *, target, daemon):
        return SimpleNamespace(start=lambda: self.tasks.append(target))

    def _idle_add(self, callback, *args):
        self.callbacks.append((callback, args))
        return 1

    def read(self):
        self.tasks.pop(0)()

    def publish(self):
        callback, args = self.callbacks.pop(0)
        return callback(*args)
