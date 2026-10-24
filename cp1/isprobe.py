import re,subprocess,sys
wt,mod,test=sys.argv[1:4]
p=f'{wt}/server/{mod}.py'
bak=open(p).read().split('\n')
pat=re.compile(r'type\(([^()]+(?:\([^()]*\))?[^()]*)\) is (not )?(\w+)')
for i,l in enumerate(bak):
    if l.strip().startswith('#'): continue
    for m in pat.finditer(l):
        neg=m.group(2)
        new=f'{"not " if neg else ""}isinstance({m.group(1)}, {m.group(3)})'
        L=list(bak);L[i]=l[:m.start()]+new+l[m.end():]
        open(p,'w').write('\n'.join(L))
        r=subprocess.run(f'cd {wt} && COLUMNS=300 timeout 250 /tmp/v/bin/python -m pytest -x -q -rf -p no:cacheprovider {test} 2>&1|grep -E "^FAILED|passed|failed"|cut -c1-170',shell=True,capture_output=True,text=True)
        print(i+1,l.strip()[:60],'=>',r.stdout.strip().replace('\n',' | ')[:190],flush=True)
open(p,'w').write('\n'.join(bak))
