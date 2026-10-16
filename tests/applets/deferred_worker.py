"""Control actual background-worker reads and GTK delivery independently."""

from types import SimpleNamespace

from docking.applets.worker import BackgroundWorker


class DeferredWorker:
    def __init__(self):
        self.tasks = []
        self.idles = []
        self.worker = BackgroundWorker(thread_factory=self._thread, idle_add=self._idle)

    def _thread(self, *, target, daemon):
        return SimpleNamespace(start=lambda: self.tasks.append(target))

    def _idle(self, callback, *args):
        self.idles.append((callback, args))
        return len(self.idles)

    def read(self):
        self.tasks.pop(0)()

    def publish(self):
        callback, args = self.idles.pop(0)
        callback(*args)

    def drain(self):
        for _ in range(20):
            if not self.tasks and not self.idles:
                return
            if self.tasks:
                self.read()
            if self.idles:
                self.publish()
        raise AssertionError("Worker kept submitting requests")
