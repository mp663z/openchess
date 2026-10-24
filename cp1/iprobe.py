import subprocess,sys,ast
wt,mod,test,surv,out=sys.argv[1:6]
p=f'{wt}/server/{mod}.py'
bak=open(p).read().split('\n')
rows=[ast.literal_eval(l) for l in open(surv) if l.startswith('(')]
res=open(out,'w')
for n,a,b in rows:
    L=list(bak);assert a in L[n-1],(n,a);L[n-1]=L[n-1].replace(a,b,1)
    open(p,'w').write('\n'.join(L))
    r=subprocess.run(f'cd {wt} && timeout 200 /tmp/v/bin/python -m pytest -x -q -p no:cacheprovider {test} 2>&1|tail -1',shell=True,capture_output=True,text=True)
    res.write(f'{n}\t{b[:70]}\t=> {r.stdout.strip()}\n');res.flush()
open(p,'w').write('\n'.join(bak))
