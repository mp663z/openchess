import re,subprocess,sys,ast
bak=open('/tmp/rs.bak').read().split('\n')
rows=[l for l in open('/tmp/fz-jobs_restart.txt') if l.startswith('(')]
for r in rows:
    n,a,b=ast.literal_eval(r)
    L=list(bak);assert a in L[n-1],(n,a);L[n-1]=L[n-1].replace(a,b,1)
    open('server/jobs_restart.py','w').write('\n'.join(L))
    p=subprocess.run('timeout 100 python -m pytest -x -q -p no:cacheprovider tests/test_t0352_restart_fuzz_fault.py 2>&1|tail -1',shell=True,capture_output=True,text=True)
    print(n,b[:60],'=>',p.stdout.strip(),flush=True)
open('server/jobs_restart.py','w').write('\n'.join(bak))
