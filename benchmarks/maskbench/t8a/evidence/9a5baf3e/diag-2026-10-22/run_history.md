# T8a run history: work dir t8a-work-9a5baf3e-w12 (core 9a5baf3e)

Diagnostics note, NOT part of the frozen harness. Sources: `run.log`,
`diag/watchdog.csv`, `diag/watchdog.log`, `out/<engine>/log.txt`, and the
host journal (systemd-logind / journal stopped markers).

## Engine segments (wall clock, UTC)

| engine | segment | exit | duration |
|---|---|---|---:|
| blg-spec-v1 | 2026-10-15T11:18:11 -> 11:19:50 | 0 | 1m39s (embedded smoke) |
| blg-spec-v1 | 2026-10-15T11:20:38 -> 18:54:21 | cut (poweroff) | 7h33m |
| blg-spec-v1 | 2026-10-16T00:16:58 -> 08:57:00 | 143 (stop) | 8h40m |
| blg-spec-v1 | 2026-10-16T19:36:27 -> 2026-10-17T07:49:11 | cut | 12h13m |
| blg-spec-v1 | 2026-10-17T07:57:12 -> 11:34:36 | 143 (stop) | 3h37m |
| blg-spec-v1 | 2026-10-17T15:19:52 -> 15:32:55 | 143 (stop) | 13m |
| blg-spec-v1 | 2026-10-17T18:48:16 -> 22:00:29 | 0 | 3h12m |
| **blg-spec-v1 total** | | | **35h29m** (35.48h full passes + 1m39s smoke) |
| llg-pinned | 2026-10-17T22:00:29 -> 22:03:30 | 0 | 3m01s |
| xgr-pinned | 2026-10-17T22:03:30 -> 2026-10-18T06:53:41 | killed by watchdog | 8h50m |
| xgr-pinned | 2026-10-18T09:28:18 -> 09:29:03 | 143 (stop) | 45s |
| xgr-pinned | 2026-10-18T09:29:09 -> 09:31:11 | 143 (stop) | 2m02s |
| xgr-pinned | 2026-10-18T18:28:47 -> 20:07:52 | 0 | 1h39m |
| **xgr-pinned total** | | | **10h32m** |
| llg-current | 2026-10-18T20:07:52 -> 20:11:12 | 0 | 3m20s |
| xgr-current | 2026-10-18T20:11:12 -> 20:21:38 | 0 | 10m26s |

The report's per-engine timing columns do not exist; the engine log's final
`Total time` line covers only the **last** segment (for blg-spec-v1:
11526.02 s after the 2026-10-17T18:48:16 resume). The table above is the
run's full wall-clock accounting.

## Interruptions and recovery

1. **2026-10-15T18:54:21Z** - host poweroff (journal: systemd-logind
   "System is powering down", journal stopped 18:54:32.887600). Not a
   crash: a clean shutdown of the machine. Resume 2026-10-16T00:16:58Z;
   interrupted markers removed, files re-ran; no corrupted records.
2. **2026-10-16T08:57:00Z** - owner-requested stop (exit 143).
   Resume 2026-10-16T19:36:27Z.
3. **2026-10-17T07:49:11Z** - abrupt host cut (no shutdown markers, no
   pstore dump, no MCE/thermal/OOM). Resume 2026-10-17T07:57:12Z.
4. **2026-10-17T11:34:36Z / 15:32:55Z** - owner-requested stops (exit 143).
5. **2026-10-18T06:53:41Z** - **watchdog kill**. `diag/watchdog.csv` last
   sample: total 33,600 MB > limit 32,768 MB, 15 processes, max process
   15,277 MB. The xgrammar (xgr-pinned) runners accumulate memory as files
   are processed; the blg phase peaked near 9 GB, so the 32 GiB guard was
   sized for it. The guard killed wrapper, driver and all runners
   (`WATCHDOG_KILL total=33600MB limit=32768MB` in `diag/watchdog.log`).
   Data integrity: 9,749 completed records readable, 278 confirmed
   timeouts, 12 interrupted markers; zero unreadable JSON.
   Recovery: the guard limit was raised to **49,152 MB** (operational
   guard only; RLIMIT_AS 40 GB per process, threads, profile, serializer,
   tokenizer and wheel unchanged), and the run resumed at
   2026-10-18T18:28:47Z; xgr-pinned finished at 20:07:52Z.
6. **2026-10-18T09:28-09:31** - two short restarts while fixing the
   interrupted-marker cleanup: `t8a_ctl.sh` used to call `resume_check.py`
   with its default engine (`blg-spec-v1`), so stale markers of the active
   engine were not cleaned. Fixed to iterate over every output directory
   without `.done`.

After every resume the queue was rebuilt from disk and matched the frozen
counts exactly (e.g. `Total files: 1196 of 11306` after the last stop:
9,832 results + 278 timeouts = 10,110). No result file was lost or
rewritten; all five engines finished with `exit=0` and the report was
written at 2026-10-18T20:21:58Z (`T8A_RUN_DONE not_accepted`).

## Verdict of the watchdog episode

The xgr-pinned stop was caused by the operational guard, not by the host:
the host stayed up (uptime from 18:46Z), and the same engine on the same
files finished after the resume. The guard is part of the evidence of
this run and must be quoted with the xgr-pinned timing.
