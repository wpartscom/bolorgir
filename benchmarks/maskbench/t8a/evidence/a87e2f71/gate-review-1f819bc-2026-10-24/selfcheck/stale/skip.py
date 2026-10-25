import importlib.util, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("bench", sys.argv[1])
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
def no_core():
    print('{"status": "SKIP", "reason": "synthetic missing dependency"}')
    raise SystemExit(0)
b.bc.require_core = no_core
sys.argv = ["bench_adr0007.py", "--skip-warm-ext", "--out", sys.argv[2]]
b.main()
