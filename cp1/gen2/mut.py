import re,subprocess,sys,shutil,os
mod,tests=sys.argv[1],sys.argv[2:]
import os
root=os.environ.get("MROOT","/home/sandbox/openchess/")
p=root+f"server/{mod}.py"
src=open(p).read().split("\n")
swaps=[("<=","<"),(">=",">"),(" < "," <= "),(" > "," >= "),("==","!="),("!=","=="),(" and "," or "),(" or "," and "),
("return False","return True"),("return True","return False"),("is None","is not None"),("is not None","is None"),
("+ 1","+ 0"),("- 1","- 0"),("not ","")," ".join([""]*1) and ("min(","max("),("max(","min("),("//","*"),(" * "," + "),("ensure_ascii=True","ensure_ascii=False")]
swaps=[x for x in swaps if isinstance(x,tuple)]
start=next(i for i,l in enumerate(src) if l.startswith("_CC ="))
surv=[];tot=0;crash=[];sem=0
for i in range(start,len(src)):
    l=src[i]
    if l.strip().startswith(("#",'"""')) : continue
    cands=list(swaps)
    for m in re.finditer(r"(?<![\w.])(\d+)(?![\w.])",l):
        v=int(m.group(1))
        if v>1 and not l.strip().startswith(("from","import")):
            cands.append((m.group(1),str(v+1)));cands.append((m.group(1),str(v-1)))
    cands+=[("2**53 - 1","2**53 - 2"),("2**53 - 2","2**53 - 1")]
    for a,b in cands:
        for m in re.finditer(re.escape(a),l):
            nl=l[:m.start()]+b+l[m.end():]
            if nl==l: continue
            tot+=1
            new=src[:i]+[nl]+src[i+1:]
            open(p,"w").write("\n".join(new))
            subprocess.run("find . -name __pycache__ -prune -exec rm -rf {} +",shell=True,cwd=root,capture_output=True)
            r=subprocess.run(["/tmp/v/bin/python","-m","pytest","-q","-x","-p","no:cacheprovider"]+tests,cwd=root,capture_output=True,text=True)
            if r.returncode==0: surv.append((i+1,l.strip(),nl.strip()))
            else:
                errs=[x for x in r.stdout.splitlines() if x.startswith("E  ")]
                first=errs[0][1:].strip() if errs else ""
                import re as _re
                if _re.match(r"^(TypeError|KeyError|AttributeError|IndexError|ValueError|RecursionError|NameError|ZeroDivisionError|OverflowError|UnicodeError|RuntimeError|UnicodeEncodeError|UnicodeDecodeError)\b",first):
                    crash.append((i+1,nl.strip(),first[:80]))
                else:
                    sem+=1
open(p,"w").write("\n".join(src))
print("mutants",tot,"survivors",len(surv),"semantic_kills",sem,"crash_only",len(crash))
for c in crash: print("CRASH",c)
for s in surv: print(s)
