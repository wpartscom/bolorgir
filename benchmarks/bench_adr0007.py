#!/usr/bin/env python3
"""ADR-0007 measurements and the mask fast-path gate.

Measures probe cold/warm legs, the DFS cost model and the pinned MaskBench
slice against benchmarks/baseline_slice.json, then compares the fast path
on/off over every visited mask, terminal state and instance termination
before any timing number is accepted. Warm acceptance applies the ADR-0007
protocol and fails the run on an unmet condition.

Everything goes through tests/blg_ctypes.py; BLG_LIB_PATH selects the
library under test. Output: JSON on stdout plus a summary on stderr. Run
from the project root with HF_HUB_OFFLINE=1 and
PYTHONPATH=python:benchmarks:tests. See docs/adr/ADR-0007-mask-fast-path.md.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import bench_common as bc  # noqa: E402

MASKBENCH_DATA = os.path.join(
    ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
    "maskbench", "maskbench", "data")
if not os.path.isdir(MASKBENCH_DATA):
    MASKBENCH_DATA = os.path.join(
        ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
        "maskbench", "data")

# Pinned MaskBench slice (ADR-0007 benchmark gate): deterministic, changes
# never. BFCL files are 586 of the 624 compiling files of the pinned run;
# most individual files do NOT compile under canonical-v1 (empty {}
# subschemas), so the slice is the first --maskbench-files candidates of
# this list that DO compile (selection is deterministic: fixed order,
# fixed count).
MASKBENCH_SLICE = [f"BFCL_java_{i}" for i in range(129)]

MASK_FAST_PATH_OFF = 2


def _read_text(path):
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path):
    value = _read_text(path)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cpu_conditions(cpus=None):
    """The CPU placement/frequency conditions the timings belong to.

    ADR-0007: a warm acceptance number is only meaningful together with
    the CPUs it ran on and the frequency policy in force. Nothing here
    changes the machine; unreadable files stay null and are reported.
    """
    if cpus is None:
        try:
            cpus = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            cpus = list(range(os.cpu_count() or 1))
    model = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    governors, cur_freq, max_freq = {}, {}, {}
    for cpu in cpus:
        base = os.path.join("/sys/devices/system/cpu",
                            "cpu%d" % cpu, "cpufreq")
        governors[str(cpu)] = _read_text(
            os.path.join(base, "scaling_governor"))
        cur_freq[str(cpu)] = _read_int(
            os.path.join(base, "scaling_cur_freq"))
        max_freq[str(cpu)] = _read_int(
            os.path.join(base, "cpuinfo_max_freq"))
    return {"model": model, "affinity": cpus, "governors": governors,
            "cur_freq_khz": cur_freq, "max_freq_khz": max_freq}


def load_conditions():
    """Load average, runnable count and CPU pressure (competing work)."""
    out = {}
    try:
        out["loadavg"] = list(os.getloadavg())
    except OSError:
        out["loadavg"] = None
    try:
        with open("/proc/stat", encoding="ascii") as f:
            for line in f:
                if line.startswith("procs_running "):
                    out["procs_running"] = int(line.split()[1])
                    break
    except OSError:
        pass
    try:
        with open("/proc/pressure/cpu", encoding="ascii") as f:
            for line in f:
                kind, *fields = line.split()
                parts = dict(field.split("=", 1) for field in fields)
                entry = {k: float(parts[k]) for k in
                         ("avg10", "avg60", "avg300") if k in parts}
                if kind == "some":
                    out["cpu_pressure_some"] = entry
                elif kind == "full":
                    out["cpu_pressure_full"] = entry
    except (OSError, ValueError):
        pass
    return out


def _finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def current_cpu():
    """The CPU this thread last ran on, when the platform exposes it.

    `os.sched_getcpu` is not available in every CPython build (it was
    missing on the review machine); `/proc/self/stat` field 39 is the
    fallback, so a fixed affinity can still be checked against an
    independent observation.
    """
    getter = getattr(os, "sched_getcpu", None)
    if getter is not None:
        try:
            return getter()
        except OSError:
            pass
    try:
        with open("/proc/self/stat", encoding="ascii") as f:
            data = f.read()
        rest = data[data.rfind(")") + 2:].split()
        # After comm, rest[0] is field 3 (state); processor is field 39.
        return int(rest[36])
    except (OSError, ValueError, IndexError):
        return None


def measurement_snapshot():
    """Everything that must be recorded before/after a timed leg."""
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    return {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cpu_affinity": affinity,
            "cpu_now": current_cpu(),
            "cpu": cpu_conditions(affinity),
            "load": load_conditions()}


def core_version(zc=None):
    if zc is not None and getattr(zc, "__version__", None):
        return zc.__version__
    try:
        from importlib.metadata import version
        return version("bolorgir")
    except Exception:
        return None


def select_compiling(blg, spec, names, count):
    """First `count` names whose schema compiles (one throwaway context)."""
    ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                      memory_limit_bytes=512 << 20,
                      mask_fast_path=MASK_FAST_PATH_OFF)
    picked = []
    try:
        for name in names:
            if len(picked) >= count:
                break
            path = os.path.join(MASKBENCH_DATA, name + ".json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            try:
                grammar = blg.compile_schema(ctx, doc["schema"])
            except blg.CoreFailure:
                continue
            blg.grammar_release(grammar)
            picked.append(name)
    finally:
        ctx.destroy()
    return picked


def load_bundle(zc, manifest):
    tok_spec = manifest["tokenizer_128k"]
    tf = bc.import_or_skip("transformers")
    if tf is None:
        bc.skip("transformers is not available")
    hf_tok = tf.AutoTokenizer.from_pretrained(
        tok_spec["name"], revision=tok_spec["revision"])
    return hf_tok, zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)


def make_spec(blg, bundle):
    import types
    return types.SimpleNamespace(
        tokens=[bundle.token_bytes(i) for i in range(bundle.vocab_size)],
        vocab_size=bundle.vocab_size,
        eos_ids=list(bundle.eos_ids),
        special_ids=list(bundle.special_ids),
        mask_words=(bundle.vocab_size + 31) // 32)


def prefix_token_ids(bundle, prefix_bytes):
    by_bytes = {}
    for i in range(bundle.vocab_size):
        by_bytes.setdefault(bundle.token_bytes(i), i)
    ids = []
    pos = 0
    while pos < len(prefix_bytes):
        for end in range(len(prefix_bytes), pos, -1):
            tid = by_bytes.get(prefix_bytes[pos:end])
            if tid is not None:
                ids.append(tid)
                pos = end
                break
        else:
            raise SystemExit(f"vocabulary cannot spell prefix at {pos}")
    return ids


def open_session(blg, spec, schema, fast_path, mode=None, workers=1):
    mode = blg.BLG_MODE_ADAPTIVE if mode is None else mode
    ctx = blg.Context(spec, mode=mode,
                      memory_limit_bytes=512 << 20,
                      cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                      work_limit_ops=1 << 60,
                      mask_fast_path=fast_path,
                      max_workers=workers)
    grammar = blg.compile_schema(ctx, schema)
    return ctx, grammar


def mask_at(blg, spec, schema, prefix_ids, fast_path, workers=1):
    """One fresh context/session: (status, words, work_ops, core_ns, wall_ns)."""
    ctx, grammar = open_session(blg, spec, schema, fast_path, workers=workers)
    try:
        session = blg.Session(ctx, grammar)
        for tid in prefix_ids:
            assert session.accept(tid) == blg.BLG_OK
        s0 = ctx.get_stats()
        t0 = bc.now_ns()
        status, words = session.fill_mask_words()
        wall_ns = bc.now_ns() - t0
        s1 = ctx.get_stats()
        session.destroy()
        return {"status": status, "words": words,
                "work_ops": s1.work_ops_total - s0.work_ops_total,
                "core_ns": s1.mask_ns_total - s0.mask_ns_total,
                "wall_ns": wall_ns}
    finally:
        blg.grammar_release(grammar)
        ctx.destroy()


def bench_probe_cold(blg, spec, bundle, probe, repeats):
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    out = {}
    for fp, name in ((MASK_FAST_PATH_OFF, "off"), (0, "on")):
        wall, ops, core = [], [], []
        for _ in range(repeats):
            r = mask_at(blg, spec, probe["schema"], ids, fp)
            assert r["status"] == blg.BLG_OK
            wall.append(r["wall_ns"])
            ops.append(r["work_ops"])
            core.append(r["core_ns"])
        out[name] = {"fill_wall_ns": bc.percentile_stats(wall),
                     "core_ns": bc.percentile_stats(core),
                     "work_ops": bc.percentile_stats(ops)}
    return out


def raw_fill(blg, session, buf):
    """One blg_fill_mask C call into a reusable buffer (no list building)."""
    import ctypes
    err = blg.new_error()
    return blg.LIB.blg_fill_mask(session.handle, buf, len(buf),
                                 ctypes.byref(err))


def bench_probe_warm(blg, spec, bundle, probe, observations):
    """Cache-hit path: one populating fill per context, then timed hits.

    Times the raw C call with a preallocated mask buffer: the Python-side
    list conversion of fill_mask_words (~150 us at 128k) would swamp the
    sub-microsecond cache-hit path the warm guard is about.
    """
    import ctypes
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    buf = (ctypes.c_uint32 * spec.mask_words)()
    out = {}
    for fp, name in ((MASK_FAST_PATH_OFF, "off"), (0, "on")):
        ctx, grammar = open_session(blg, spec, probe["schema"], fp)
        try:
            session = blg.Session(ctx, grammar)
            for tid in ids:
                assert session.accept(tid) == blg.BLG_OK
            assert raw_fill(blg, session, buf) == blg.BLG_OK  # populating
            session.destroy()
            hits0 = ctx.get_stats().cache_hits
            fill_ns = []
            for _ in range(observations):
                session = blg.Session(ctx, grammar)
                for tid in ids:
                    assert session.accept(tid) == blg.BLG_OK
                t0 = bc.now_ns()
                status = raw_fill(blg, session, buf)
                assert status == blg.BLG_OK
                fill_ns.append(bc.now_ns() - t0)
                session.destroy()
            hits = ctx.get_stats().cache_hits - hits0
            out[name] = {"observations": observations, "cache_hits": hits,
                         "all_hits": hits == observations,
                         "fill_wall_ns": bc.percentile_stats(fill_ns)}
        finally:
            blg.grammar_release(grammar)
            ctx.destroy()
    return out


def _terminal_error(exc):
    """A terminal-call exception as a recorded value.

    The optional numeric `status` is kept when present, but the fact that
    the call raised is never lost: an exception without `status`
    (`RuntimeError`, `MemoryError`) is recorded by type, and a numeric
    status is recorded next to the type as well.
    """
    status = getattr(exc, "status", None)
    name = type(exc).__name__
    return name if status is None else "%s(%s)" % (name, status)


def maskbench_traces(hf_tok, blg, spec, names, fast_path):
    """Replay the pinned slice; per-mask wall times + the complete trace.

    Returns (tbm_us list, trace, stats). `trace` is not a sample: it holds
    every visited input state, so the gate compares every state, keyed by
    (file, instance, step). The terminal check is recorded separately and
    never enters the timed input sequence.

      trace["states"][i] = (file, instance, step, fill_status,
                            words_bytes, accept_code)

    `words_bytes` is the exact mask in compact uint32 form (empty when the
    fill failed); `accept_code` is the return code of accepting the token
    that follows the mask (None when the fill failed).

      trace["instances"][i] = (file, instance, expected_valid, consumed,
                               observed_valid, termination)

    `consumed` means every input token was accepted; `observed_valid` is
    the semantic outcome (True = completed, False = rejected, None = an
    input/completion error, never folded into invalidity). `termination`
    is "completed", "rejected:..." or "error:...".

      trace["terminals"][i] = (file, instance, tail_status, tail_words,
                               can_end, can_end_error, finish,
                               finish_error)

    The tail mask is the post-input (EOS/termination) mask; it is compared
    but not timed. `finish == BLG_ERR_WRONG_STATE` with `can_end == False`
    is a correct rejection; any other non-zero finish, a failed tail fill
    or a failed can_end is a completion error, and it is counted even when
    the document was already rejected at the input stage. A raised
    exception is recorded by type and, when present, its numeric `status`;
    an exception without `status` is still a fault. Compile errors are
    skipped and counted (the pinned run's 5.5% canonical-v1 coverage
    applies here too).
    """
    tbm_ns = []
    states = []
    terminals = []
    instances = []
    stats = {"files": 0, "compiled": 0, "compile_error": 0, "instances": 0,
             "masks": 0, "fill_errors": 0, "accept_errors": 0,
             "terminal_masks": 0, "terminal_fill_errors": 0,
             "input_errors": 0, "completion_errors": 0,
             "validation_error": 0}
    err_invalid = getattr(blg, "BLG_ERR_INVALID_TOKEN", 6)
    err_dead = getattr(blg, "BLG_ERR_DEAD_END", 7)
    err_wrong = getattr(blg, "BLG_ERR_WRONG_STATE", 11)
    for name in names:
        path = os.path.join(MASKBENCH_DATA, name + ".json")
        if not os.path.exists(path):
            continue
        stats["files"] += 1
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        try:
            ctx, grammar = open_session(blg, spec, doc["schema"], fast_path)
        except blg.CoreFailure:
            stats["compile_error"] += 1
            continue
        stats["compiled"] += 1
        try:
            for test in doc["tests"]:
                instance = json.dumps(test["data"], ensure_ascii=False,
                                      separators=(",", ":"))
                tokens = hf_tok.encode(instance, add_special_tokens=False)
                session = blg.Session(ctx, grammar)
                stats["instances"] += 1
                number = stats["instances"]
                consumed = True
                rejected = False
                input_error = None
                accept_code = None
                for step, tid in enumerate(tokens):
                    t0 = bc.now_ns()
                    status, words = session.fill_mask_words()
                    ns = bc.now_ns() - t0
                    if status != blg.BLG_OK:
                        stats["fill_errors"] += 1
                        states.append((name, number, step, status, b"", None))
                        consumed = False
                        input_error = "fill:%d" % status
                        break
                    stats["masks"] += 1
                    tbm_ns.append(ns)
                    code = session.accept(tid)
                    accept_code = code
                    states.append((name, number, step, status,
                                   array.array("I", words).tobytes(), code))
                    if code != blg.BLG_OK:
                        stats["accept_errors"] += 1
                        consumed = False
                        if code in (err_invalid, err_dead):
                            rejected = True
                        else:
                            stats["input_errors"] += 1
                            input_error = "accept:%d" % code
                        break

                # Terminal check: the post-input mask, can_end and finish.
                # It is not timed and not part of the 564 input states.
                tail_error = None
                try:
                    tail_status, tail_words = session.fill_mask_words()
                except Exception as exc:  # diagnostic harness
                    tail_status, tail_words = None, []
                    tail_error = _terminal_error(exc)
                if tail_status == blg.BLG_OK:
                    stats["terminal_masks"] += 1
                    tail_blob = array.array("I", tail_words).tobytes()
                else:
                    stats["terminal_fill_errors"] += 1
                    tail_blob = b""
                can_end = None
                can_end_error = None
                try:
                    can_end = bool(session.can_end())
                except Exception as exc:
                    # The fact of the exception is the fault; `status` is
                    # optional diagnostics and must not decide whether a
                    # failure was seen.
                    can_end_error = _terminal_error(exc)
                finish = None
                finish_error = None
                try:
                    finish = session.finish()
                except Exception as exc:
                    finish_error = _terminal_error(exc)

                # Operational faults of the terminal checks are counted
                # on their own. A semantic rejection - or an input error
                # already recorded above - must not hide a failed tail
                # fill, a can_end/finish exception or an abnormal finish
                # code; the two questions are never folded together.
                terminal_fault = None
                if tail_error is not None:
                    terminal_fault = "tail-exception:%s" % tail_error
                elif tail_status != blg.BLG_OK:
                    terminal_fault = "tail-fill:%r" % (tail_status,)
                elif can_end_error is not None:
                    terminal_fault = "can-end-exception:%s" % can_end_error
                elif finish_error is not None:
                    terminal_fault = "finish-exception:%s" % finish_error
                elif finish not in (blg.BLG_OK, err_wrong):
                    terminal_fault = "finish:%r" % (finish,)
                if terminal_fault is not None:
                    stats["completion_errors"] += 1

                observed = None
                if input_error is not None:
                    termination = "error:" + input_error
                elif terminal_fault is not None:
                    termination = "error:terminal:" + terminal_fault
                elif rejected:
                    observed = False
                    termination = "rejected:accept:%d" % accept_code
                elif can_end is True and finish == blg.BLG_OK:
                    observed = True
                    termination = "completed"
                elif can_end is False and finish == err_wrong:
                    observed = False
                    termination = "rejected:finish:%d" % finish
                else:
                    stats["completion_errors"] += 1
                    termination = "error:finish:%r" % (finish,)
                if observed is not None and observed != bool(test["valid"]):
                    stats["validation_error"] += 1
                instances.append((name, number, bool(test["valid"]),
                                  consumed, observed, termination))
                terminals.append((name, number, tail_status, tail_blob,
                                  can_end, can_end_error, finish,
                                  finish_error))
                session.destroy()
        finally:
            blg.grammar_release(grammar)
            ctx.destroy()
    return tbm_ns, {"states": states, "terminals": terminals,
                    "instances": instances}, stats


def _words_list(blob):
    if not blob:
        return []
    words = array.array("I")
    words.frombytes(blob)
    return words.tolist()


def compare_maskbench(off, on, limit=20):
    """Compare two complete arms state by state and instance by instance.

    Every visited mask, every fill status, every accept return code, every
    terminal (tail mask, can_end, finish) and every instance termination
    takes part. The state sequence is keyed by (file, instance, step): a
    missing or extra state is a structural divergence, never a silently
    shorter comparison. A fill failure or a validation mismatch that both
    arms share is still a failure.
    """
    problems = []
    so, sn = off["states"], on["states"]
    if len(so) != len(sn):
        problems.append({"kind": "state_count", "off": len(so),
                         "on": len(sn)})
    for i, (a, b) in enumerate(zip(so, sn)):
        if a[:3] != b[:3]:
            problems.append({"kind": "structure", "index": i,
                             "off": list(a[:3]), "on": list(b[:3])})
            break
        if a[3] != b[3] or a[5] != b[5]:
            problem = {"kind": "status", "index": i,
                       "case": list(a[:3]),
                       "off": {"fill": a[3], "accept": a[5]},
                       "on": {"fill": b[3], "accept": b[5]}}
            if a[4] != b[4]:
                problem["off_words"] = _words_list(a[4])
                problem["on_words"] = _words_list(b[4])
            problems.append(problem)
        elif a[4] != b[4]:
            problems.append({"kind": "mask", "index": i,
                             "case": list(a[:3]),
                             "off_words": _words_list(a[4]),
                             "on_words": _words_list(b[4])})
    io, inn = off["instances"], on["instances"]
    if len(io) != len(inn):
        problems.append({"kind": "instance_count", "off": len(io),
                         "on": len(inn)})
    for a, b in zip(io, inn):
        if a != b:
            problems.append({"kind": "instance", "off": list(a),
                             "on": list(b)})
    for arm, trace in (("off", off), ("on", on)):
        bad = [list(rec) for rec in trace["instances"]
               if rec[4] is not None and rec[4] != rec[2]]
        if bad:
            problems.append({"kind": "validation", "arm": arm,
                             "mismatches": bad[:limit]})
    to, tn = off["terminals"], on["terminals"]
    if len(to) != len(tn):
        problems.append({"kind": "terminal_count", "off": len(to),
                         "on": len(tn)})
    for a, b in zip(to, tn):
        if a[:2] != b[:2]:
            problems.append({"kind": "terminal-structure",
                             "off": list(a[:2]), "on": list(b[:2])})
            break
        if a[2] != b[2] or a[3] != b[3] or a[4:] != b[4:]:
            problem = {"kind": "terminal", "case": list(a[:2]),
                       "off": {"tail_status": a[2], "can_end": a[4],
                               "can_end_error": a[5], "finish": a[6],
                               "finish_error": a[7]},
                       "on": {"tail_status": b[2], "can_end": b[4],
                              "can_end_error": b[5], "finish": b[6],
                              "finish_error": b[7]}}
            if a[3] != b[3]:
                problem["off_words"] = _words_list(a[3])
                problem["on_words"] = _words_list(b[3])
            problems.append(problem)
    return problems[:limit], len(problems) > limit


def maskbench_digest(trace):
    """SHA256 over every compared state, terminal and instance."""
    h = hashlib.sha256()
    for rec in trace["states"]:
        h.update(("%s/%d/%d/fill=%d/nwords=%d/accept=%s\n" % (
            rec[0], rec[1], rec[2], rec[3], len(rec[4]) // 4,
            rec[5])).encode("utf-8"))
        h.update(rec[4])
    for rec in trace["terminals"]:
        h.update(("%s/%d/tail=%s/nwords=%d/can_end=%s/can_end_error=%s/"
                  "finish=%s/finish_error=%s\n" % (
                      rec[0], rec[1], rec[2], len(rec[3]) // 4, rec[4],
                      rec[5], rec[6], rec[7])).encode("utf-8"))
        h.update(rec[3])
    for rec in trace["instances"]:
        h.update((json.dumps(list(rec), ensure_ascii=False,
                             separators=(",", ":")) + "\n").encode("utf-8"))
    return h.hexdigest()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_sha256(path):
    """Hash a library path for the report; a harness without a real .so
    (a stubbed gate self-check) records null instead of failing."""
    try:
        return _sha256(path)
    except OSError:
        return None


def _loaded_lib():
    """Path and SHA256 of the mapped libbolorgir, from /proc/self/maps.

    The C extension links `libbolorgir.so.0`; whichever object satisfies
    that SONAME is the library the timings belong to, and it need not be
    the one the parent's ctypes handle points at. The child therefore
    reports the mapped file itself and the parent rejects a mismatch.
    """
    path = None
    with open("/proc/self/maps", encoding="utf-8", errors="replace") as f:
        for line in f:
            if "libbolorgir" not in line:
                continue
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            cand = parts[5].strip()
            if cand.endswith(" (deleted)"):
                cand = cand[:-len(" (deleted)")]
            if cand.startswith("/"):
                path = os.path.realpath(cand)
                break
    if path is None:
        return {"path": None, "sha256": None}
    return {"path": path, "sha256": _sha256(path)}


def warm_ext_helper(probe_id, observations, manifest):
    """Subprocess entry: warm-path timing through the C-extension API.

    The baseline warm numbers (0.85 us) were recorded through
    bolorgir._core (a compiled extension, ~0.1 us call overhead); a
    ctypes loop has ~5 us of Python-side overhead that would swamp the
    sub-microsecond cache-hit path. The extension loads libbolorgir.so.0
    by SONAME, so the parent puts the exact file under test into
    LD_PRELOAD (a directory with only libbolorgir.so.0.1.0 does not
    satisfy that SONAME) and passes its SHA256 in
    BLG_EXPECT_LIB_SHA256; this process verifies the mapped file before
    timing and fails otherwise. BLG_MASK_FAST_PATH=0 is the kill switch
    (ADR-0007 Decision 5).

    BLG_WARM_CPU pins the process (and its children) to one CPU before the
    first measurement; the placement, frequency policy, load and CPU
    pressure are recorded immediately before and after the timed loop, and
    the mapped library is hashed again after it, so a report can be tied
    to the conditions it belongs to.
    """
    pin = os.environ.get("BLG_WARM_CPU")
    if pin not in (None, ""):
        try:
            os.sched_setaffinity(0, {int(pin)})
        except (AttributeError, OSError, ValueError) as exc:
            print(json.dumps({"status": "FAIL", "pin": pin,
                              "error": "%s: %s" % (type(exc).__name__, exc)}))
            raise SystemExit(2)
    zc = bc.require_core()
    loaded = _loaded_lib()
    expect = os.environ.get("BLG_EXPECT_LIB_SHA256", "")
    if expect and loaded["sha256"] != expect:
        print(json.dumps({"status": "FAIL", "loaded": loaded,
                          "expected_sha256": expect}))
        raise SystemExit(2)
    _, bundle = load_bundle(zc, manifest)
    probe = next(p for p in manifest["probes"] if p["id"] == probe_id)
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    eng = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    constraint = eng.compile(probe["schema"])
    session = constraint.create_session()
    for tid in ids:
        session.accept_token(tid)
    session.fill_mask()  # populating, discarded
    session.close()
    hits0 = eng.stats()["cache_hits"]
    conditions_start = measurement_snapshot()
    conditions_start["extension_version"] = core_version(zc)
    fill_ns = []
    for _ in range(observations):
        session = constraint.create_session()
        for tid in ids:
            session.accept_token(tid)
        t0 = bc.now_ns()
        session.fill_mask()
        fill_ns.append(bc.now_ns() - t0)
        session.close()
    hits = eng.stats()["cache_hits"] - hits0
    conditions_end = measurement_snapshot()
    conditions_end["extension_version"] = core_version(zc)
    loaded_end = _loaded_lib()
    if expect and loaded_end["sha256"] != expect:
        print(json.dumps({"status": "FAIL", "loaded": loaded,
                          "loaded_end": loaded_end,
                          "expected_sha256": expect}))
        raise SystemExit(2)
    constraint.close()
    eng.close()
    print(json.dumps({"observations": observations, "cache_hits": hits,
                      "all_hits": hits == observations,
                      "loaded": loaded, "loaded_end": loaded_end,
                      "expected_sha256": expect or None,
                      "conditions": {"start": conditions_start,
                                     "end": conditions_end},
                      "fill_wall_ns": bc.percentile_stats(fill_ns)}))


def warm_via_extension(manifest, probe_id, observations, lib_path,
                       on_first=False, cpu=None):
    """Run warm_ext_helper in two subprocesses: fast path OFF (env kill
    switch) and ON (default). Returns {"off": ..., "on": ...}.

    `on_first` swaps the leg order so repeated measurements can alternate
    it (the acceptance protocol fixes alternating order across repeats).
    `cpu` pins each child to one CPU (BLG_WARM_CPU) and the parent rejects
    a leg that did not run on exactly that CPU.

    The exact file under test goes into LD_PRELOAD, its directory also to
    the front of LD_LIBRARY_PATH, and its SHA256 into
    BLG_EXPECT_LIB_SHA256; each child verifies the mapped library and the
    parent rejects a leg whose `loaded.sha256` differs.
    """
    import subprocess
    import shutil
    import tempfile
    expect = _sha256(lib_path)
    # The repository path contains spaces, and LD_PRELOAD splits on them:
    # expose the exact file under a space-free temporary directory instead.
    preload_dir = tempfile.mkdtemp(prefix="blg-warm-")
    try:
        preload = os.path.join(preload_dir, "libbolorgir.so.0.1.0")
        os.symlink(os.path.abspath(lib_path), preload)
        os.symlink(os.path.abspath(lib_path),
                   os.path.join(preload_dir, "libbolorgir.so.0"))
        out = {}
        legs = ((True, "off"), (False, "on"))
        if on_first:
            legs = ((False, "on"), (True, "off"))
        for fp_off, name in legs:
            env = dict(os.environ)
            env["HF_HUB_OFFLINE"] = "1"
            env["LD_PRELOAD"] = preload
            env["BLG_EXPECT_LIB_SHA256"] = expect
            env["LD_LIBRARY_PATH"] = preload_dir + os.pathsep + env.get(
                "LD_LIBRARY_PATH", "")
            if cpu is not None:
                env["BLG_WARM_CPU"] = str(cpu)
            else:
                env.pop("BLG_WARM_CPU", None)
            if fp_off:
                env["BLG_MASK_FAST_PATH"] = "0"
            else:
                env.pop("BLG_MASK_FAST_PATH", None)
            proc = subprocess.run(
                [sys.executable, os.path.abspath(__file__),
                 "--warm-ext-helper", probe_id, str(observations)],
                env=env, capture_output=True, text=True, check=True)
            out[name] = json.loads(proc.stdout.strip().splitlines()[-1])
            got = out[name].get("loaded", {}).get("sha256")
            if got != expect:
                raise SystemExit(
                    f"warm_ext {name} loaded {got}, expected {expect}")
            got_end = (out[name].get("loaded_end") or {}).get("sha256")
            if got_end is not None and got_end != expect:
                raise SystemExit(
                    "warm_ext %s loaded_end %s, expected %s"
                    % (name, got_end, expect))
            if cpu is not None:
                conditions = out[name].get("conditions") or {}
                for when in ("start", "end"):
                    affinity = (conditions.get(when) or {}).get(
                        "cpu_affinity")
                    if affinity != [int(cpu)]:
                        raise SystemExit(
                            "warm_ext %s %s affinity %r, expected CPU %s"
                            % (name, when, affinity, cpu))
        return out
    finally:
        shutil.rmtree(preload_dir, ignore_errors=True)


def gate_artifact(dest_dir, tag, payload):
    """Save complete gate evidence before a failed run exits.

    ADR-0007 Decision 4: a divergence must leave the case and both full
    mask word lists behind, not only a message. This writer is used for
    the MaskBench trace comparison (every state and instance) and for
    shared failures such as a fill error or a validation mismatch.
    """
    if dest_dir is None:
        dest_dir = os.path.join(os.getcwd(), "mask-artifacts")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "gate-failure-%s-%d.json"
                        % (tag, int(time.time() * 1000)))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print("gate failure artifact: %s" % path, file=sys.stderr, flush=True)
    return path


def mask_artifact(dest_dir, tag, case, left, right):
    """Save the failing masks to a file before the gate returns an error.

    ADR-0007 Decision 4: a divergence must leave the case and both full
    mask word lists behind, not only a message.
    """
    import time
    if dest_dir is None:
        dest_dir = os.path.join(os.getcwd(), "mask-artifacts")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "mask-failure-%s-%d.json"
                        % (tag, int(time.time() * 1000)))
    payload = {"tag": tag, "case": case,
               "expected": {"status": left.get("status"),
                            "words": left.get("words")},
               "actual": {"status": right.get("status"),
                          "words": right.get("words")}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print("mask failure artifact: %s" % path, file=sys.stderr, flush=True)
    return path


def validate_warm_acceptance(manifest, probes, args, warm_cpu, expect,
                             parent=None, declared=None):
    """Judge the published warm legs against the fixed protocol.

    The verdict is a property of the written data, not of the process that
    produced it: a live run and a saved JSON (`--verify-warm-json`) pass
    through this function unchanged. Every check fails closed - a missing
    after-loop hash, a non-finite mean, or a leg that holds fewer
    observations than the protocol declares is a failure, never an absent
    value that meets a limit. Nothing is sampled and nothing is discarded.

    The expected protocol comes from the invocation, not from the data:
    `args.warm_repeats`, `args.warm_observations`, the threshold, the
    pinned CPU, the load/pressure limits and the expected governor are the
    acceptance criteria. `declared` is the protocol block stored in a
    saved JSON; when given it must match, otherwise the data cannot belong
    to the measurement being accepted.
    """
    threshold = args.warm_mean_threshold_ns
    probe_ids = [p["id"] for p in manifest["probes"]]
    protocol = {"repeats": args.warm_repeats,
                "observations": args.warm_observations,
                "threshold_ns": threshold,
                "probes": probe_ids,
                "cpu": warm_cpu,
                "governor": args.warm_governor,
                "lib_sha256": expect}
    live = parent is None
    if live:
        parent = measurement_snapshot()
    rows = []
    problems = []
    condition_problems = []
    leg_conditions = []
    all_legs = []
    for pid in probe_ids:
        entry = probes.get(pid) or {}
        for rd in entry.get("warm_ext_repeats") or []:
            for name in ("off", "on"):
                all_legs.append((pid, rd, name, rd.get(name) or {}))
    any_conditions = any((leg.get("conditions") or {})
                         for _, _, _, leg in all_legs)
    conditions_complete = bool(all_legs) and any_conditions
    if args.warm_repeats < 1:
        # A zero-round run measures nothing; it must not pass vacuously.
        problems.append("warm acceptance needs at least one complete round")
    # The protocol fixes the whole leg matrix: every probe, every round,
    # both legs, exactly once. A shorter list is a missing measurement,
    # not a smaller sample.
    expected_legs = [(pid, rnd, name) for pid in probe_ids
                     for rnd in range(1, args.warm_repeats + 1)
                     for name in ("off", "on")]
    got_legs = [(pid, rd.get("round"), name)
                for pid, rd, name, _ in all_legs]
    if sorted(got_legs) != sorted(expected_legs):
        conditions_complete = False
        missing = [x for x in expected_legs if x not in got_legs]
        extra = [x for x in got_legs if x not in expected_legs]
        problems.append("leg matrix has %d legs, expected %d (missing %r, "
                        "unexpected %r)" % (len(got_legs),
                                            len(expected_legs),
                                            missing[:4], extra[:4]))
    for pid in probe_ids:
        entry = probes.get(pid) or {}
        for rd in entry.get("warm_ext_repeats") or []:
            rnd = rd.get("round")
            if not isinstance(rnd, int) or rnd < 1:
                conditions_complete = False
                condition_problems.append(
                    "%s: invalid round %r" % (pid, rnd))
            elif rd.get("order") != ("off,on" if rnd % 2 else "on,off"):
                conditions_complete = False
                condition_problems.append(
                    "%s round %d: leg order %r, expected %r (alternating)"
                    % (pid, rnd, rd.get("order"),
                       "off,on" if rnd % 2 else "on,off"))
    if not any_conditions:
        condition_problems.append(
            "saved-data replay: measurement conditions were not "
            "recorded (no new measurement)")
    elif warm_cpu is None:
        condition_problems.append(
            "warm acceptance needs a fixed CPU placement "
            "(--warm-cpu or BLG_WARM_CPU)")
    elif live:
        allowed = None
        try:
            allowed = sorted(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            pass
        if allowed is not None and warm_cpu not in allowed:
            condition_problems.append(
                "CPU %d is outside the process affinity %r"
                % (warm_cpu, allowed))
    for pid, rd, name, leg in all_legs:
        st = leg.get("fill_wall_ns") or {}
        cond = leg.get("conditions") or {}
        cond_start = cond.get("start") or {}
        cond_end = cond.get("end") or {}
        loaded = leg.get("loaded") or {}
        loaded_end = leg.get("loaded_end") or {}
        loaded_sha = loaded.get("sha256")
        loaded_end_sha = loaded_end.get("sha256")
        declared_sha = leg.get("expected_sha256")
        observations = leg.get("observations")
        hits = leg.get("cache_hits")
        fill_count = st.get("count")
        mean = st.get("mean")
        p50 = st.get("p50")
        mean_ok = _finite_number(mean) and float(mean) >= 0.0
        p50_ok = _finite_number(p50) and float(p50) >= 0.0
        hits_ok = (leg.get("all_hits") is True and
                   observations == hits == fill_count ==
                   args.warm_observations)
        # Both mapped hashes are mandatory: the one taken before the timed
        # loop and the one taken after it. `expected_sha256` is the value
        # the helper was told to expect; it is recorded for audit but the
        # two observations are what the check rests on.
        hash_ok = loaded_sha == expect and loaded_end_sha == expect
        start_aff = cond_start.get("cpu_affinity")
        end_aff = cond_end.get("cpu_affinity")
        gov_start = (cond_start.get("cpu") or {}).get("governors") or {}
        gov_end = (cond_end.get("cpu") or {}).get("governors") or {}
        if warm_cpu is not None:
            gov_start = gov_start.get(str(warm_cpu))
            gov_end = gov_end.get(str(warm_cpu))
        else:
            gov_start = gov_end = None
        leg_conditions.append({
            "probe": pid, "round": rd.get("round"), "leg": name,
            "cpu_affinity_start": start_aff,
            "cpu_affinity_end": end_aff,
            "cpu_now_start": cond_start.get("cpu_now"),
            "cpu_now_end": cond_end.get("cpu_now"),
            "extension_version_start": cond_start.get("extension_version"),
            "extension_version_end": cond_end.get("extension_version"),
            "governors": (cond_start.get("cpu") or {}).get("governors"),
            "governor_start": gov_start,
            "governor_end": gov_end,
            "cur_freq_khz": (cond_start.get("cpu") or {}).get(
                "cur_freq_khz"),
            "load_start": cond_start.get("load"),
            "load_end": cond_end.get("load"),
            "loaded_sha256": loaded_sha,
            "loaded_end_sha256": loaded_end_sha,
            "expected_sha256": declared_sha})
        rows.append({"probe": pid, "round": rd.get("round"),
                     "order": rd.get("order"), "leg": name,
                     "observations": observations, "cache_hits": hits,
                     "fill_count": fill_count,
                     "mean_ns": mean, "p50_ns": p50,
                     "mean_ok": mean_ok, "p50_ok": p50_ok,
                     "hits_ok": hits_ok, "hash_ok": hash_ok,
                     "cpu_affinity_start": start_aff,
                     "cpu_affinity_end": end_aff,
                     "cpu_now_start": cond_start.get("cpu_now"),
                     "cpu_now_end": cond_end.get("cpu_now")})
        if not hits_ok:
            conditions_complete = False
            problems.append(
                "%s round %r %s: observations %r, cache_hits %r, "
                "fill count %r, expected %d (all_hits %r)"
                % (pid, rd.get("round"), name, observations, hits,
                   fill_count, args.warm_observations,
                   leg.get("all_hits")))
        if not hash_ok:
            conditions_complete = False
            problems.append(
                "%s round %r %s: loaded %r / after loop %r, expected %s"
                % (pid, rd.get("round"), name, loaded_sha,
                   loaded_end_sha, expect))
        if not mean_ok:
            problems.append(
                "%s round %r %s: mean %r is not a finite nonnegative "
                "number" % (pid, rd.get("round"), name, mean))
        if not p50_ok:
            problems.append(
                "%s round %r %s: p50 %r is not a finite nonnegative "
                "number" % (pid, rd.get("round"), name, p50))
        if not any_conditions:
            continue
        # Mandatory: both observations must name the fixed CPU.
        for aff in (start_aff, end_aff):
            if not isinstance(aff, list) or not aff:
                conditions_complete = False
        if warm_cpu is not None:
            for when, aff in (("start", start_aff), ("end", end_aff)):
                if aff != [warm_cpu]:
                    condition_problems.append(
                        "%s round %r %s %s: affinity %r, expected [%d]"
                        % (pid, rd.get("round"), name, when, aff, warm_cpu))
        # Mandatory numeric load and CPU pressure in both snapshots;
        # a missing observation is not a value that meets the limit.
        for when, snap in (("start", cond_start), ("end", cond_end)):
            load = snap.get("load")
            if not isinstance(load, dict):
                conditions_complete = False
                condition_problems.append(
                    "%s round %r %s %s: missing load snapshot"
                    % (pid, rd.get("round"), name, when))
            else:
                loadavg = load.get("loadavg")
                if (not isinstance(loadavg, (list, tuple)) or
                        len(loadavg) < 3 or
                        not all(_finite_number(x) for x in loadavg[:3])):
                    conditions_complete = False
                    condition_problems.append(
                        "%s round %r %s %s: missing or invalid loadavg"
                        % (pid, rd.get("round"), name, when))
                elif loadavg[0] > args.warm_max_load1:
                    condition_problems.append(
                        "%s round %r %s %s: load1 %.2f > %.2f"
                        % (pid, rd.get("round"), name, when, loadavg[0],
                           args.warm_max_load1))
                psi = (load.get("cpu_pressure_some") or {}).get("avg10")
                if not _finite_number(psi):
                    conditions_complete = False
                    condition_problems.append(
                        "%s round %r %s %s: missing or invalid cpu "
                        "pressure avg10"
                        % (pid, rd.get("round"), name, when))
                elif psi > args.warm_max_cpu_pressure:
                    condition_problems.append(
                        "%s round %r %s %s: cpu pressure avg10 %.2f > "
                        "%.2f" % (pid, rd.get("round"), name, when, psi,
                                  args.warm_max_cpu_pressure))
            if not snap.get("extension_version"):
                conditions_complete = False
                condition_problems.append(
                    "%s round %r %s %s: missing extension version"
                    % (pid, rd.get("round"), name, when))
            if warm_cpu is not None:
                govs = (snap.get("cpu") or {}).get("governors") or {}
                if not govs.get(str(warm_cpu)):
                    conditions_complete = False
                    condition_problems.append(
                        "%s round %r %s %s: missing scaling governor for "
                        "CPU %d" % (pid, rd.get("round"), name, when,
                                    warm_cpu))
                # The recorded placement is checked against an
                # independent observation, not only against the request.
                cpu_now = snap.get("cpu_now")
                if cpu_now != warm_cpu:
                    conditions_complete = False
                    condition_problems.append(
                        "%s round %r %s %s: cpu_now %r, expected CPU %d"
                        % (pid, rd.get("round"), name, when, cpu_now,
                           warm_cpu))
        if warm_cpu is not None:
            if gov_start != gov_end:
                conditions_complete = False
                condition_problems.append(
                    "%s round %r %s: governor changed %r -> %r during "
                    "the leg" % (pid, rd.get("round"), name, gov_start,
                                 gov_end))
            if args.warm_governor and gov_start != args.warm_governor:
                condition_problems.append(
                    "%s round %r %s: governor %r, expected policy %r"
                    % (pid, rd.get("round"), name, gov_start,
                       args.warm_governor))
    versions = {c.get("extension_version_start") for c in leg_conditions}
    versions |= {c.get("extension_version_end") for c in leg_conditions}
    if any_conditions and (None in versions or len(versions) != 1):
        conditions_complete = False
        condition_problems.append(
            "extension version is not recorded consistently: %r"
            % (sorted(v for v in versions if v is not None),))
    conditions = {
        "requested_cpu": warm_cpu,
        "recorded": any_conditions,
        "complete": conditions_complete,
        "limits": {"max_load1": args.warm_max_load1,
                   "max_cpu_pressure_avg10": args.warm_max_cpu_pressure,
                   "expected_governor": args.warm_governor},
        "parent": parent,
        "legs": leg_conditions,
        "ok": not condition_problems,
        "problems": condition_problems,
    }
    problems.extend("conditions: " + p for p in condition_problems)
    on_means = [(r["probe"], r["round"], r["mean_ns"])
                for r in rows if r["leg"] == "on"]
    for pid, rnd, mean in on_means:
        if (_finite_number(mean) and float(mean) >= 0.0 and
                float(mean) > threshold):
            problems.append("%s round %r on: mean %.1f ns > %d ns"
                            % (pid, rnd, float(mean), threshold))
    protocol["on_legs"] = len(on_means)
    if declared is not None:
        # A saved JSON must describe the protocol it was accepted under.
        for key in ("repeats", "observations", "threshold_ns", "cpu",
                    "lib_sha256"):
            if key not in declared:
                problems.append("recorded protocol lacks %r" % (key,))
            elif declared.get(key) != protocol[key]:
                problems.append("recorded protocol %s = %r, expected %r"
                                % (key, declared.get(key), protocol[key]))
        if declared.get("probes") != probe_ids:
            problems.append("recorded protocol probes = %r, expected %r"
                            % (declared.get("probes"), probe_ids))
    return {
        "threshold_ns": threshold,
        "protocol": protocol,
        "on_legs": len(on_means),
        "on_means_ns": [m for _, _, m in on_means],
        "on_p50_ns": [r["p50_ns"] for r in rows if r["leg"] == "on"],
        "pass": not problems,
        "problems": problems,
        "conditions": conditions,
        "rows": rows,
    }


def verify_warm_json(path, manifest, args, warm_cpu):
    """Re-judge the warm acceptance stored in a gate JSON.

    This is the check the review asked for: saved data are judged exactly
    as a live run would be, with the protocol expected from the command
    line, without producing a new measurement. The file's own protocol
    block is compared with the expectation, so a JSON that claims a
    shorter protocol cannot accept itself.
    """
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    problems = []
    acc = doc.get("warm_acceptance")
    if not isinstance(acc, dict):
        problems.append("no warm_acceptance block in %s" % (path,))
        acc = {}
    expect = None
    if args.lib:
        expect = _sha256(args.lib)
    if expect is None:
        expect = (doc.get("environment") or {}).get("lib_sha256")
    if not isinstance(expect, str) or len(expect) != 64:
        problems.append("expected library SHA256 is unavailable: pass --lib "
                        "or keep environment.lib_sha256")
        expect = ""
    elif (doc.get("environment") or {}).get("lib_sha256") not in (None, expect):
        problems.append("environment.lib_sha256 %r, expected %r"
                        % ((doc.get("environment") or {}).get("lib_sha256"),
                           expect))
    if doc.get("status") != "OK":
        problems.append("source status is %r, expected 'OK'"
                        % (doc.get("status"),))
    source_conditions = acc.get("conditions") or {}
    verdict = validate_warm_acceptance(
        manifest, doc.get("probes") or {}, args, warm_cpu, expect,
        parent=source_conditions.get("parent") or {},
        declared=acc.get("protocol"))
    problems.extend(verdict["problems"])
    if source_conditions.get("requested_cpu") != warm_cpu:
        problems.append("recorded requested CPU %r, expected %r"
                        % (source_conditions.get("requested_cpu"), warm_cpu))
    limits = source_conditions.get("limits") or {}
    for key, want in (("max_load1", args.warm_max_load1),
                      ("max_cpu_pressure_avg10",
                       args.warm_max_cpu_pressure)):
        if limits.get(key) != want:
            problems.append("recorded limit %s = %r, expected %r"
                            % (key, limits.get(key), want))
    report = {
        "kind": "warm-verify",
        "source": os.path.abspath(path),
        "source_sha256": _sha256(path),
        "source_status": doc.get("status"),
        "source_run_id": doc.get("run_id"),
        "verified_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "measured_now": False,
        "expected_lib_sha256": expect,
        "recorded_protocol": acc.get("protocol"),
        "protocol": verdict["protocol"],
        "threshold_ns": verdict["threshold_ns"],
        "on_legs": verdict["on_legs"],
        "on_means_ns": verdict["on_means_ns"],
        "on_p50_ns": verdict["on_p50_ns"],
        "conditions": verdict["conditions"],
        "rows": verdict["rows"],
        "pass": not problems,
        "problems": problems,
    }
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)),
                    exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    if report["pass"]:
        print("warm verify: PASS (%d legs, means %s ns; %s)" % (
            report["on_legs"],
            ", ".join("%.1f" % m for m in report["on_means_ns"]),
            path), file=sys.stderr, flush=True)
        return 0
    print("warm verify: FAIL (%s)" % "; ".join(problems),
          file=sys.stderr, flush=True)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=os.path.join(HERE, "baseline_slice.json"))
    ap.add_argument("--cold-repeats", type=int, default=10)
    ap.add_argument("--warm-observations", type=int, default=2000)
    ap.add_argument("--mask-artifacts", default=None,
                    help="directory for failing-mask dumps (ADR-0007 D4); "
                         "defaults to ./mask-artifacts")
    ap.add_argument("--warm-repeats", type=int, default=1,
                    help="complete off/on warm_ext rounds; the leg order "
                         "alternates across rounds")
    ap.add_argument("--warm-mean-threshold-ns", type=int, default=1300,
                    help="acceptance: every warm_ext.on mean must be <= "
                         "this (ADR-0007 warm guard)")
    ap.add_argument("--warm-cpu", type=int, default=None,
                    help="fixed CPU placement for the warm_ext children "
                         "(or BLG_WARM_CPU); warm acceptance refuses to "
                         "run without one")
    ap.add_argument("--warm-max-load1", type=float, default=1.0,
                    help="max 1-minute load average recorded during a "
                         "warm leg; a higher sample fails acceptance "
                         "(conditions must be fixed before measuring)")
    ap.add_argument("--warm-max-cpu-pressure", type=float, default=1.0,
                    help="max /proc/pressure/cpu some.avg10 (percent) "
                         "during a warm leg")
    ap.add_argument("--warm-governor", default=None,
                    help="expected scaling governor for the pinned CPU at "
                         "the start and the end of every warm leg; a "
                         "missing or different policy fails acceptance")
    ap.add_argument("--verify-warm-json", default=None, metavar="PATH",
                    help="re-judge the warm_acceptance block of a saved "
                         "gate JSON with the same validator; no timing is "
                         "run, --lib supplies the expected library hash")
    ap.add_argument("--lib", default=None,
                    help="library file whose SHA256 is the expected hash "
                         "in --verify-warm-json mode")
    ap.add_argument("--maskbench-files", type=int, default=len(MASKBENCH_SLICE))
    ap.add_argument("--skip-maskbench", action="store_true")
    ap.add_argument("--skip-warm-ext", action="store_true")
    ap.add_argument("--run-id", default=os.environ.get("BLG_RUN_ID"),
                    help="identity of this run; the matrix passes it via "
                         "BLG_RUN_ID and requires it in the written JSON")
    ap.add_argument("--warm-ext-helper", nargs=2, metavar=("PROBE_ID", "OBS"),
                    default=None, help=argparse.SUPPRESS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    if args.warm_ext_helper:
        probe_id, obs = args.warm_ext_helper
        warm_ext_helper(probe_id, int(obs), manifest)
        return

    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    warm_cpu = args.warm_cpu
    if warm_cpu is None:
        env_cpu = os.environ.get("BLG_WARM_CPU")
        if env_cpu not in (None, ""):
            warm_cpu = int(env_cpu)

    if args.verify_warm_json:
        raise SystemExit(verify_warm_json(args.verify_warm_json, manifest,
                                          args, warm_cpu))

    zc = bc.require_core()
    import blg_ctypes as blg

    hf_tok, bundle = load_bundle(zc, manifest)
    spec = make_spec(blg, bundle)
    lib_path = os.path.realpath(blg.LIB._path)
    loaded = _loaded_lib()
    out = {"status": "OK", "case": "adr0007_mask_fast_path",
           "run_id": args.run_id,
           "lib": blg.LIB._path,
           "environment": {
               "started_utc": started_utc,
               "finished_utc": None,
               "kernel": platform.release(),
               "python": platform.python_version(),
               "core_version": core_version(zc),
               "lib_path": lib_path,
               "lib_sha256": _safe_sha256(lib_path),
               "loaded_lib": loaded,
               "cpu": cpu_conditions(),
               "load": load_conditions()},
           "vocab_size": bundle.vocab_size}

    # ---- benchmark gate: on/off mask equality over every visited state --
    print("gate: probe states on/off equality ...", file=sys.stderr, flush=True)
    gate_states = 0
    for probe in manifest["probes"]:
        ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
        r_off = mask_at(blg, spec, probe["schema"], ids, MASK_FAST_PATH_OFF)
        r_on = mask_at(blg, spec, probe["schema"], ids, 0)
        r_on8 = mask_at(blg, spec, probe["schema"], ids, 0, workers=8)
        if (r_off["status"], r_off["words"]) != (r_on["status"], r_on["words"]):
            mask_artifact(args.mask_artifacts, "probe-fast-path",
                          {"probe": probe["id"], "schema": probe["schema"],
                           "ids": ids}, r_off, r_on)
            raise SystemExit(f"GATE FAILURE: fast path diverges on {probe['id']}")
        if r_on["words"] != r_on8["words"]:
            mask_artifact(args.mask_artifacts, "probe-workers",
                          {"probe": probe["id"], "schema": probe["schema"],
                           "ids": ids},
                          {"status": r_on["status"], "words": r_on["words"]},
                          {"status": r_on8["status"], "words": r_on8["words"]})
            raise SystemExit(f"GATE FAILURE: max_workers diverges on {probe['id']}")
        gate_states += 2
    out["gate"] = {"probe_states": gate_states, "status": "PASS"}

    # ---- probes: cold + warm, off vs on ----
    out["probes"] = {}
    for probe in manifest["probes"]:
        print(f"cold probe {probe['id']} off/on ...", file=sys.stderr, flush=True)
        cold = bench_probe_cold(blg, spec, bundle, probe, args.cold_repeats)
        print(f"warm probe {probe['id']} off/on ...", file=sys.stderr, flush=True)
        warm = bench_probe_warm(blg, spec, bundle, probe, args.warm_observations)
        entry = {"cold": cold, "warm": warm}
        if not args.skip_warm_ext:
            rounds = []
            for r in range(args.warm_repeats):
                on_first = (r % 2) == 1
                order = "on,off" if on_first else "off,on"
                print("warm-ext probe %s round %d/%d (%s) (C extension) ..."
                      % (probe["id"], r + 1, args.warm_repeats, order),
                      file=sys.stderr, flush=True)
                kwargs = {"on_first": on_first}
                try:
                    import inspect
                    if "cpu" in inspect.signature(
                            warm_via_extension).parameters:
                        kwargs["cpu"] = warm_cpu
                except (TypeError, ValueError):
                    pass
                legs = warm_via_extension(
                    manifest, probe["id"], args.warm_observations,
                    blg.LIB._path, **kwargs)
                rounds.append({"round": r + 1, "order": order,
                               "off": legs["off"], "on": legs["on"]})
            # `warm_ext` keeps the historical off/on shape (first round);
            # every round is published and judged by the acceptance block.
            if rounds:
                entry["warm_ext"] = {"off": rounds[0]["off"],
                                     "on": rounds[0]["on"]}
            entry["warm_ext_repeats"] = rounds
        out["probes"][probe["id"]] = entry

    # ---- warm acceptance: every repeat of every probe, no discards ----
    if not args.skip_warm_ext and out.get("probes"):
        out["warm_acceptance"] = validate_warm_acceptance(
            manifest, out["probes"], args, warm_cpu,
            _sha256(blg.LIB._path))

    # ---- MaskBench pinned slice ----
    if not args.skip_maskbench:
        print("selecting compiling maskbench files ...", file=sys.stderr,
              flush=True)
        names = select_compiling(blg, spec, MASKBENCH_SLICE,
                                 args.maskbench_files)
        print(f"maskbench slice ({len(names)} files: {', '.join(names)}) "
              "OFF run ...", file=sys.stderr, flush=True)
        tbm_off, trace_off, stats_off = maskbench_traces(
            hf_tok, blg, spec, names, MASK_FAST_PATH_OFF)
        print("maskbench slice ON run ...", file=sys.stderr, flush=True)
        tbm_on, trace_on, stats_on = maskbench_traces(
            hf_tok, blg, spec, names, 0)
        # Gate: every visited mask, every fill/accept status, every
        # terminal (tail mask, can_end, finish) and every instance
        # termination, keyed by (file, instance, ...). Nothing is sampled;
        # a shorter comparison can no longer hide a fault.
        problems, truncated = compare_maskbench(trace_off, trace_on)
        for arm, stats in (("off", stats_off), ("on", stats_on)):
            for key, kind in (("fill_errors", "fill"),
                              ("terminal_fill_errors", "terminal-fill"),
                              ("input_errors", "input"),
                              ("completion_errors", "completion")):
                if stats[key]:
                    problems.append({"kind": kind, "arm": arm,
                                     "count": stats[key]})
        if not trace_off["states"]:
            problems.append({"kind": "empty", "detail":
                             "no MaskBench state was compared"})
        terminal_complete = (len(trace_off["terminals"]) ==
                             len(trace_off["instances"]) ==
                             len(trace_on["terminals"]) ==
                             len(trace_on["instances"]))
        if not terminal_complete:
            problems.append({"kind": "terminal-count",
                             "off_terminals": len(trace_off["terminals"]),
                             "off_instances": len(trace_off["instances"]),
                             "on_terminals": len(trace_on["terminals"]),
                             "on_instances": len(trace_on["instances"])})
        digest = maskbench_digest(trace_off)
        out["gate"].update({
            "maskbench_states": len(trace_off["states"]),
            "maskbench_instances": len(trace_off["instances"]),
            "maskbench_terminal_states": len(trace_off["terminals"]),
            "maskbench_terminal_complete": terminal_complete,
            "maskbench_fill_errors": stats_off["fill_errors"] + stats_on["fill_errors"],
            "maskbench_terminal_fill_errors": (stats_off["terminal_fill_errors"] +
                                                stats_on["terminal_fill_errors"]),
            "maskbench_input_errors": stats_off["input_errors"] + stats_on["input_errors"],
            "maskbench_completion_errors": (stats_off["completion_errors"] +
                                            stats_on["completion_errors"]),
            "maskbench_accept_errors": stats_off["accept_errors"] + stats_on["accept_errors"],
            "maskbench_validation_errors": (stats_off["validation_error"] +
                                            stats_on["validation_error"]),
            "maskbench_trace_sha256": digest,
        })
        out["maskbench_slice"] = {
            "files": names,
            "off": {"tbm_us": bc.percentile_stats([n / 1000 for n in tbm_off]),
                    "stats": stats_off, "trace_sha256": digest},
            "on": {"tbm_us": bc.percentile_stats([n / 1000 for n in tbm_on]),
                   "stats": stats_on, "trace_sha256": digest},
        }
        if problems:
            gate_artifact(args.mask_artifacts, "maskbench", {
                "file": "(all)",
                "case": {"states_compared": len(trace_off["states"]),
                         "instances": len(trace_off["instances"])},
                "divergences": problems,
                "truncated": truncated,
                "stats_off": stats_off,
                "stats_on": stats_on,
                "trace_sha256": digest,
            })
            kinds = {}
            for p in problems:
                kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
            raise SystemExit(
                "GATE FAILURE: maskbench gate failed (%s; %d state(s), "
                "%d terminal(s), %d instance(s) compared)"
                % (", ".join("%s x%d" % (k, v)
                             for k, v in sorted(kinds.items())),
                   len(trace_off["states"]), len(trace_off["terminals"]),
                   len(trace_off["instances"])))

    # The warm guard is an acceptance criterion, not a report: a failed
    # protocol must fail the run even though the measurement completed.
    # The matrix additionally re-reads `warm_acceptance.pass` from the
    # written JSON, so neither a swallowed exit code nor a stale report can
    # turn the row green.
    acc = out.get("warm_acceptance")
    warm_failed = acc is not None and not acc["pass"]
    if warm_failed:
        out["status"] = "FAIL"
        out["failure"] = {"stage": "warm_acceptance",
                          "problems": acc["problems"]}
    out["environment"]["finished_utc"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"wrote {args.out}", file=sys.stderr)
    bc.emit(out)
    if warm_failed:
        print("WARM ACCEPTANCE FAILED: " + "; ".join(acc["problems"]),
              file=sys.stderr, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
