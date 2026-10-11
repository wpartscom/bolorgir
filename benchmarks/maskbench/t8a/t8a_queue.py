"""Work queue of the T8a driver: every file is pending, running or finished.

scripts/run_maskbench.py hands its workers the files without an output:
when its list runs empty, a worker takes again every file without an
output, also those other workers are still running. The same files then
run in several processes at once (smoke t8a-smoke-1: the 20 files of the
smoke went to every one of the 4 workers, progress 300%). Here a chunk
takes pending files only; a file of a finished chunk without an output
goes back to pending, so it runs again only after its chunk ended. A
worker with nothing pending waits while other chunks run, since they may
give files back.

A file with an output (a result, or MaskBench's pending marker, written
when the file starts and kept after a timeout) never runs again. Handing
a file to a chunk is not an attempt to process it: a chunk ends at the
first timeout (MaskBench's SIGALRM ends the process), and the files the
runner had not reached go back without an output (full run t8a-6e62e0d1,
2026-10-10: a file never started in two chunks cut by timeouts stopped
the run as "attempted 2 times"). So there is no count of attempts.

The run stops (failure is set, no new chunk starts, the running ones end)
when a chunk gives none of its files an output, or when the chunk runner
raises. Every chunk after which the run goes on gives at least one file
an output, so the files without one decrease and the run ends.
"""

import random
import threading
import time


class FileQueue:
    def __init__(self, files, has_output, shuffle=random.shuffle):
        self.total = len(files)
        self.pending = [f for f in files if not has_output(f)]
        shuffle(self.pending)
        self.finished = self.total - len(self.pending)
        self.running = set()
        self.failure = None
        self._has_output = has_output
        self._shuffle = shuffle
        self._cond = threading.Condition()

    def take(self, workers, chunk_size):
        """The next chunk (the size scripts/run_maskbench.py takes), or []
        when the run is over or stopped."""
        with self._cond:
            while self.failure is None and not self.pending and self.running:
                self._cond.wait()
            if self.failure is not None or not self.pending:
                return []
            n = min(chunk_size, len(self.pending) // workers + 20)
            chunk = self.pending[:n]
            del self.pending[:n]
            self.running.update(chunk)
            return chunk

    def finish(self, chunk, error=None):
        """Records the end of a chunk; returns (with output, without)."""
        missing = [f for f in chunk if not self._has_output(f)]
        with self._cond:
            self.running.difference_update(chunk)
            self.finished += len(chunk) - len(missing)
            if error is not None:
                self._stop(f"the chunk runner failed: {error}")
            elif missing and len(missing) == len(chunk):
                self._stop(f"a chunk of {len(chunk)} files produced no output")
            else:
                self.pending.extend(missing)
                self._shuffle(self.pending)
            self._cond.notify_all()
            return len(chunk) - len(missing), len(missing)

    def _stop(self, reason):
        if self.failure is None:
            self.failure = reason


def run_queue(queue, run_chunk, workers, chunk_size=100, log=print):
    """Runs run_chunk(chunk) in `workers` threads until the queue is
    empty or stopped; returns queue.failure."""
    log(f"Total files: {len(queue.pending)} of {queue.total}")
    if not queue.pending:
        log("All files processed.")
        return None
    workers = min(workers, len(queue.pending))
    t0 = time.monotonic()

    def worker():
        while True:
            chunk = queue.take(workers, chunk_size)
            if not chunk:
                return
            error = None
            try:
                run_chunk(chunk)
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
            done, missing = queue.finish(chunk, error)
            with queue._cond:
                state = (f"{len(queue.pending)} pending, {len(queue.running)} running, "
                         f"{queue.finished}/{queue.total} done")
            log(f"{time.monotonic() - t0:.1f}s {done} + {missing}; {state}")

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log(f"Total time: {time.monotonic() - t0:.2f}s")
    return queue.failure
