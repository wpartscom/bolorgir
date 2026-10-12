"""Argument parsing of the T9 overhead benchmark
(benchmarks/bench_rollback_overhead.py): build flags start
with "-" and are given as --flags-a=VALUE. Only the parser and the
library-free checks run here; no library is loaded and nothing is
measured."""

import importlib.util
import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "bench_rollback_overhead", os.path.join(ROOT, "benchmarks", "bench_rollback_overhead.py"))
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

ONE = "-Drelease=true"
MANY = "-Drelease=true -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=baseline"


def _parse(*argv):
    return bench.parser().parse_args(["--lib-a", "a.so", "--lib-b", "b.so", *argv])


@pytest.mark.parametrize("flags", [ONE, MANY])
def test_flags_with_equals_keep_the_whole_value(flags):
    args = _parse(f"--flags-a={flags}", f"--flags-b={flags}", "--check-bound")
    assert args.flags_a == flags and args.flags_b == flags
    assert bench.argument_problems(args) == []


def test_flags_as_a_separate_argument_are_refused_by_argparse():
    # The form with flags as a separate argument: "-Drelease=true" is
    # read as an unknown option and --flags-a has no value.
    with pytest.raises(SystemExit) as e:
        _parse("--flags-a", ONE, "--flags-b", ONE)
    assert e.value.code == 2


def test_check_bound_needs_equal_flags_depth_zero_and_rounds():
    args = _parse(f"--flags-a={ONE}", f"--flags-b={MANY}", "--check-bound", "--depth", "4",
                  "--rounds", "5")
    problems = bench.argument_problems(args)
    assert len(problems) == 3
    assert any("flags differ" in p for p in problems)
    assert any("--depth must be 0" in p for p in problems)
    assert any("10 rounds" in p for p in problems)


def test_check_args_loads_no_library(capsys):
    # The library paths do not exist: --check-args never opens them.
    rc = bench.main(["--lib-a", "/nonexistent/a.so", "--lib-b", "/nonexistent/b.so",
                     f"--flags-a={MANY}", f"--flags-b={MANY}", "--check-bound", "--check-args"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["flags_a"] == MANY and out["problems"] == []
    rc = bench.main(["--lib-a", "/nonexistent/a.so", "--lib-b", "/nonexistent/b.so",
                     "--check-bound", "--check-args"])
    assert rc == bench.EXIT_UNUSABLE
