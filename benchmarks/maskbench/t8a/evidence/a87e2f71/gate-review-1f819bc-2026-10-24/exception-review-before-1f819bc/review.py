import contextlib, copy, importlib.util, io, json, os, pathlib, sys, types
sys.dont_write_bytecode=True
ROOT=pathlib.Path.cwd(); OUT=pathlib.Path(__file__).parent

def load(name,path):
    s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
b=load('review_bench',ROOT/'benchmarks/bench_adr0007.py')
m=load('review_matrix',ROOT/'benchmarks/maskbench/t8a/matrix_adr0007.py')
lib=ROOT/'benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0'
os.environ['BLG_LIB_PATH']=str(lib)
sha=b._sha256(lib); cpu=min(os.sched_getaffinity(0)); mode='control'; seen=[]
class Context:
    def __init__(self,fp): self.fp=fp
    def destroy(self): pass
class Session:
    def __init__(self,ctx,g): self.fp=ctx.fp; self.step=0
    def fill_mask_words(self):
        seen.append(('fill',self.fp,self.step))
        if self.step==2 and mode=='tail-mask': return (0,[0] if self.fp==0 else [2])
        return 0,[1]
    def accept(self,tid):
        self.step+=1
        return 6 if mode.startswith('reject-') else 0
    def can_end(self):
        seen.append(('can_end',self.fp,self.step))
        if mode=='reject-can-end-memory-error':
            raise MemoryError('synthetic allocation failure in can_end')
        if mode=='reject-can-end-no-status':
            raise RuntimeError('synthetic can_end failure without status')
        if mode=='reject-can-end-error':
            exc=Exception('synthetic can_end failure'); exc.status=8; raise exc
        return not (mode=='cannot-end' and self.fp==0)
    def finish(self):
        if mode in ('reject-can-end-no-status','reject-can-end-memory-error'): return 11
        if mode in ('reject-finish-resource','finish-resource'): return 8
        return 0 if self.can_end() else 11
    def destroy(self): pass
blg=types.SimpleNamespace(Session=Session,Context=Context,CoreFailure=type('CoreFailure',(Exception,),{}),BLG_OK=0,BLG_CACHE_DEFAULT=0,BLG_MODE_ADAPTIVE=1,grammar_release=lambda g:None,LIB=types.SimpleNamespace(_path=str(lib)))
sys.modules['blg_ctypes']=blg
b.bc.require_core=lambda:object(); b.load_bundle=lambda *a:(types.SimpleNamespace(encode=lambda *a,**k:[0,0]),types.SimpleNamespace(vocab_size=2))
b.make_spec=lambda *a:types.SimpleNamespace(eos_ids=[1]); b.prefix_token_ids=lambda *a:[]
b.open_session=lambda blg,spec,schema,fp:(Context(fp),None)
b.mask_at=lambda *a,**kw:{'status':0,'words':[1]}; b.bench_probe_cold=lambda *a:{}; b.bench_probe_warm=lambda *a:{}
b._loaded_lib=lambda:{'path':str(lib),'sha256':sha}
corpus=OUT/'corpus'; corpus.mkdir(exist_ok=True); (corpus/'one.json').write_text(json.dumps({'schema':{},'tests':[{'data':'abc','valid':True}]}))
b.MASKBENCH_DATA=str(corpus); b.select_compiling=lambda *a:['one']

def run(name,args):
    dest=OUT/(name+'.json'); sys.argv=['bench_adr0007.py','--out',str(dest),'--run-id',name,'--mask-artifacts',str(OUT/('artifacts-'+name))]+args
    log=io.StringIO(); code=0
    with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        try: b.main()
        except SystemExit as e: code=e.code
    (OUT/(name+'.log')).write_text(log.getvalue())
    d=json.loads(dest.read_text()) if dest.exists() else None
    return code,d,dest
results=[]
for mode in ['control','cannot-end','tail-mask','finish-resource','reject-control','reject-finish-resource','reject-can-end-error','reject-can-end-no-status','reject-can-end-memory-error']:
    (corpus/'one.json').write_text(json.dumps({'schema':{},'tests':[{'data':'abc','valid':not mode.startswith('reject-')}]}))
    seen.clear(); code,d,p=run('gate-'+mode,['--skip-warm-ext'])
    verdict=None
    if d:
        job=next(j for j in m.jobs('quick',sys.executable,'zig',str(OUT)) if j['id']=='bench-gate')
        job['verdict'].update(json=str(p),run_id='gate-'+mode)
        verdict=m.check_verdict(job)
    results.append({'case':mode,'kind':'synthetic gate fault','exit':code,'gate':d['gate'] if d else None,'matrix_ok':verdict['ok'] if verdict else False,'session_calls':list(seen)})

(OUT/'review-results.json').write_text(json.dumps(results,indent=2))
print(json.dumps([{'case':r['case'],'exit':r['exit'],'completion_errors':(r['gate'] or {}).get('maskbench_completion_errors'),'matrix_ok':r['matrix_ok']} for r in results],indent=2))
