import subprocess,sys,ast
wt,mod,test,surv,out=sys.argv[1:6]
p=f'{wt}/server/{mod}.py'
bak=open(p).read().split('\n')
rows=[ast.literal_eval(l) for l in open(surv) if l.startswith('(')]
res=open(out,'w')
for n,a,b in rows:
    L=list(bak);L[n-1]=L[n-1].replace(a,b,1) if a in L[n-1] else (_ for _ in ()).throw(SystemExit(f'miss {n}'))
    open(p,'w').write('\n'.join(L))
    r=subprocess.run(f'cd {wt} && timeout 300 /tmp/v/bin/python -m pytest -q -p no:cacheprovider {test} 2>&1',shell=True,capture_output=True,text=True)
    fails=[l for l in r.stdout.splitlines() if l.startswith('FAILED')]
    foreign=[l for l in fails if 'FOREIGN-EXCEPTION' in l]
    res.write(f'{n}\t{b[:60]}\tfailed={len(fails)}\tforeign={len(foreign)}\tsemantic={len(fails)-len(foreign)}\n');res.flush()
open(p,'w').write('\n'.join(bak))
