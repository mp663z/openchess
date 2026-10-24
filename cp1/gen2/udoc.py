import sys
task,title,mod,fname,reqs,mapping,mut=sys.argv[1:8]
open(f"/home/sandbox/openchess/requirements/tasks/{task}.md","w").write(f"""# {task} {title}

## Normative requirements

{open(reqs).read().strip()}

## Requirement-to-test mapping

{open(mapping).read().strip()}

## Mutation check

{open(mut).read().strip()}
""")
