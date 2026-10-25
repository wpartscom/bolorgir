import importlib.util, json, sys, types
from pathlib import Path
sys.dont_write_bytecode = True
repo, dest = Path(sys.argv[1]), Path(sys.argv[2])
spec = importlib.util.spec_from_file_location('review_bench', repo/'benchmarks/bench_adr0007.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
saved = json.loads((repo/'benchmarks/maskbench/t8a/evidence/2d982b60/diag-2026-10-23/equiv/warm_acceptance.json').read_text())
lib = str(repo/'benchmarks/local/t8a-checkpoints/2d982b60/libbolorgir.so.0.1.0')
sys.modules['blg_ctypes'] = types.SimpleNamespace(LIB=types.SimpleNamespace(_path=lib))
b.bc.require_core = lambda: object()
b.load_bundle = lambda *a: (None, types.SimpleNamespace(vocab_size=128256))
b.make_spec = lambda *a: None
b.prefix_token_ids = lambda *a: []
b.mask_at = lambda *a, **k: {'status': 0, 'words': [1]}
b.bench_probe_cold = lambda *a: {}
b.bench_probe_warm = lambda *a: {}
rounds = {}
def replay(manifest, pid, observations, lib_path, on_first=False):
    n = rounds.get(pid, 0); rounds[pid] = n+1
    rd = saved['probes'][pid]['warm_ext_repeats'][n]
    assert rd['order'] == ('on,off' if on_first else 'off,on')
    return {'on': rd['on'], 'off': rd['off']}
b.warm_via_extension = replay
sys.argv = ['bench_adr0007.py', '--manifest', str(repo/'benchmarks/baseline_slice.json'), '--skip-maskbench', '--cold-repeats','1','--warm-observations','2000','--warm-repeats','5','--out',str(dest)]
b.main()
