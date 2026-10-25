#!/usr/bin/env python3
"""Compare complete masks from the candidate's fast and exact paths.
No repository files or benchmark records are changed.
Run: python3 repro.py /absolute/path/to/zig
Requires the existing venv-pinned and candidate zig-out shared library.
"""
import hashlib, json, os, pathlib, subprocess, sys
CASES = [
    ('uniqueItems', {'type':'array','items':{'type':'string','maxLength':1},
                     'uniqueItems':True,'minItems':2,'maxItems':2}, '["a","'),
    ('allOf', {'allOf':[{'type':'string','maxLength':1},
                       {'type':'string','maxLength':2}]}, '"'),
]
VOCAB = [bytes([n]) for n in range(256)] + [b'ab', b'aa', b'bb']
EXPECTED_SHA = '17294af13be791a06171d7cbf4c488987aed5db9b29ab1548fb7ea9f1daf488c'

def child():
    import ctypes
    import bolorgir as b
    lib = ctypes.CDLL(None)
    lib.blg_diag_counters.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
    lib.blg_diag_counters.restype = ctypes.c_size_t
    tok = b.TokenizerBundle.from_token_bytes(VOCAB)
    rows = []
    for mode in ('lazy', 'adaptive'):
        for name, schema, prefix in CASES:
            lib.blg_diag_reset()
            with b.Engine(tokenizer=tok, mode=mode) as e:
                c = e.compile(schema, tok, profile='spec-v1')
                s = c.create_session()
                for tid in prefix.encode():
                    s.accept_token(tid)
                mask = s.fill_mask()
                counters = (ctypes.c_uint64 * 17)()
                lib.blg_diag_counters(counters, 17)
                rows.append({'case': name, 'mode': mode, 'mask_hex': mask.hex(),
                             'fast_used': counters[2]})
                s.close()
                c.close()
    print(json.dumps(rows))

def main():
    root = pathlib.Path(sys.argv[1]).resolve()
    lib = root / 'zig-out/lib/libbolorgir.so.0.1.0'
    digest = hashlib.sha256(lib.read_bytes()).hexdigest()
    if digest != EXPECTED_SHA:
        raise SystemExit('Candidate changed: expected ' + EXPECTED_SHA + ', found ' + digest)
    python = root / 'benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python'
    outputs = {}
    for flag in ('0', '1'):
        env = os.environ.copy()
        env.update(BLG_MASK_FAST_PATH=flag, LD_PRELOAD='./zig-out/lib/libbolorgir.so.0.1.0',
                   PYTHONDONTWRITEBYTECODE='1')
        env.pop('BLG_MASK_NO_FILTER', None)
        env.pop('PYTHONPATH', None)
        proc = subprocess.run([str(python), str(pathlib.Path(__file__).resolve()), '--child'],
                              cwd=root, env=env, text=True, capture_output=True, timeout=30, check=True)
        outputs[flag] = json.loads(proc.stdout)
    comparisons = []
    for off, on in zip(outputs['0'], outputs['1']):
        a, b = bytes.fromhex(off['mask_hex']), bytes.fromhex(on['mask_hex'])
        extra = [i for i in range(len(VOCAB)) if b[i//8] & (1 << (i%8)) and not a[i//8] & (1 << (i%8))]
        missing = [i for i in range(len(VOCAB)) if a[i//8] & (1 << (i%8)) and not b[i//8] & (1 << (i%8))]
        comparisons.append({'case':off['case'], 'mode':off['mode'], 'equal':a == b,
                            'fast_used':on['fast_used'], 'extra_ids':extra,
                            'extra_tokens':[VOCAB[i].decode('ascii') for i in extra],
                            'missing_ids':missing})
    print(json.dumps({'core_sha256':digest, 'tokenizer':'all 256 byte tokens plus ab, aa, bb',
                      'work_limit_ops':'default', 'cases':CASES,
                      'comparisons':comparisons, 'raw':outputs}, indent=2))

if __name__ == '__main__':
    if sys.argv[1:] == ['--child']:
        child()
    else:
        main()
