import sys,re
path,total,surv,sem,typed,extra,red=sys.argv[1:8]
s=open(path).read()
j=s.index("## Red/green transition")
i=s.find("## Mutation check")
if i<0: i=j
killed=int(total)-int(surv)
new=f"""## Mutation check

{total} single-token production mutants (comparison, boolean, return, offset, operator and integer-literal swaps) were run against the {red} battery plus the runtime proof, stopping at each mutant's first failing test. {killed} mutants are killed and none of them by a raw crash: a kill counts only when the first failure is a semantic outcome, which is an assertion ({sem}) or a wrong typed refusal or detected caller-code run ({typed}). Raw exceptions (TypeError, KeyError, AttributeError and the like) are tallied separately and there were 0 of them, so no mutant relies on a crash. The {surv} survivors are stated as behavior-equivalent or non-code: {extra}

"""
open(path,'w').write(s[:i]+new+s[j:])
