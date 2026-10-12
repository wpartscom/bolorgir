"""The ECMA-262 reference (`regress`, unicode mode) in a worker process
with a memory and time limit.

regress 2025.10.1 does not terminate on some loops whose body can match
the empty string: `(?:a?)+?b` on "ac" and `((a?)+)+b` on "aac" grow its
backtracking stack until the allocation fails (a 4 GiB request, then an
abort). In the pytest process itself that took all the memory of the
machine; here it ends the worker only. A query whose worker dies or does
not answer in time returns None (no reference answer), the worker is
restarted, and the failure is kept in `failures`.

As a script it is the worker: one JSON request per line on stdin
({"pattern", "text"}; text null for a compile check), one JSON answer per
line on stdout. Only the standard library and regress are imported.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys

MEMORY_LIMIT = 1 << 30   # address space of the worker, bytes
TIMEOUT = 10.0           # seconds per query


class EcmaReference:
    def __init__(self, memory_limit=MEMORY_LIMIT, timeout=TIMEOUT):
        self.memory_limit, self.timeout = memory_limit, timeout
        self.failures = []   # (pattern, text, reason)
        self.proc = None

    def _start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-I", os.path.abspath(__file__), str(self.memory_limit)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)

    def _stop(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def _ask(self, pattern, text):
        """The worker's answer, or None after recording why there is none."""
        if self.proc is None:
            self._start()
        self.proc.stdin.write(json.dumps({"pattern": pattern, "text": text}) + "\n")
        self.proc.stdin.flush()
        ready, _, _ = select.select([self.proc.stdout], [], [], self.timeout)
        line = self.proc.stdout.readline() if ready else None
        if not line:
            if line is None:
                reason = f"no answer in {self.timeout:g} s"
            else:
                self.proc.wait()
                reason = f"worker ended with status {self.proc.returncode}"
            self._stop()
            self.failures.append((pattern, text, reason))
            return None
        return json.loads(line)

    def compiles(self, pattern):
        """Whether ^(?:pattern)$ is a valid unicode-mode ECMA pattern; None
        when the reference failed."""
        a = self._ask(pattern, None)
        return None if a is None else a["ok"]

    def full_match(self, pattern, text):
        """Whether ^(?:pattern)$ matches text; None when the reference
        failed (the case has no reference answer)."""
        a = self._ask(pattern, text)
        if a is None:
            return None
        if not a["ok"]:
            raise ValueError(f"{pattern!r} does not compile in the reference: {a['error']}")
        return a["match"]

    def close(self):
        self._stop()


def _worker(memory_limit):
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    import regress

    cache = {}
    for line in sys.stdin:
        q = json.loads(line)
        pattern, text = q["pattern"], q["text"]
        try:
            r = cache.get(pattern)
            if r is None:
                r = cache[pattern] = regress.Regex("^(?:" + pattern + ")$", "u")
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}), flush=True)
            continue
        if text is None:
            print(json.dumps({"ok": True}), flush=True)
        else:
            print(json.dumps({"ok": True, "match": r.find(text) is not None}), flush=True)


if __name__ == "__main__":
    _worker(int(sys.argv[1]))
