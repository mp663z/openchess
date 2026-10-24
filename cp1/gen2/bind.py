import sys,subprocess,re
# bind.py TASK NAME MOD REDFILE REFMOD FN ERR LINT FIX REFT OP
TASK,NAME,MOD,RED,REFMOD,FN,ERR,LINT,FIX,REFT,OP=sys.argv[1:12]
root="/home/sandbox/openchess/"
p=root+f"tests/{RED}.py"
s=open(p).read()
sha=__import__("hashlib").sha256(s.encode()).hexdigest()
old=f"{FN} = _reference.{FN}\n{ERR} = _reference.{ERR}\n"
new=f'{FN} = __import__("server.{MOD}").{MOD}.{FN}\n{ERR} = __import__("server.{MOD}").{MOD}.{ERR}\n'
if old in s:
    s=s.replace(old,new)
    sha_src=None
else:
    assert new in s
    sha=__import__("hashlib").sha256(s.replace(new,old).replace(", F401","").encode()).hexdigest()
imp=f"from tests import {REFMOD} as _reference  # noqa: E402\n"
if imp in s:
    s=s.replace(imp,imp.replace("# noqa: E402","# noqa: E402, F401"))
open(p,'w').write(s)
t=open("/tmp/gen2/runtime.tmpl").read()
for k,v in dict(TASK=TASK,NAME=NAME,MOD=MOD,RED=RED.split("_")[1].upper() if False else RED,REDMOD=RED,REFMOD=REFMOD,FN=FN,ERR=ERR,LINT=LINT,FIX=FIX,REFT=REFT,OP=OP,SHA=sha).items():
    t=t.replace("@"+k+"@",v)
open(root+f"tests/test_{TASK.lower()}_{NAME}_runtime.py","w").write(t)
print(sha)
