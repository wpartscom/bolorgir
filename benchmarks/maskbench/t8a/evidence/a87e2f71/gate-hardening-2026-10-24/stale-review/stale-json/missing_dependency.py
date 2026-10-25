import importlib.util, sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location("bench",sys.argv[1])
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
b.bc.import_or_skip=lambda package: None # inject a missing core dependency
sys.argv=["bench_adr0007.py","--skip-warm-ext","--out",sys.argv[2]]
b.main()
