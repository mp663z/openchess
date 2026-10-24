import re,sys
# usage: prod.py REF OUT MODULE_DOC LINTMOD
ref,out,doc,lint,ERR,FN=sys.argv[1:7]
s=open(ref).read()
a=s.index("_CC = yaml.safe_load(CONTRACT")
b=s.index("# -- reference end")
body=s[a:b]
body=re.sub(r"\n# -- reference begin:.*?\n\n","\n",body,flags=re.S)
body=body.replace("_REF_","MAX_" if False else "_LIM_")
body=body.replace("and len(mapping) == len(fields)\n        ","")
body=body.replace('"""The reference ','"""The ')
hdr=f'''{open(doc).read().rstrip()}

from __future__ import annotations

import hashlib
import json
import re

import yaml

from {lint} import CONTRACT, FAILURE_MAPPING

'''
body=body.replace("\n\nclass ", "\n\n__all__ = [\"FAILURE_MAPPING\", \"@ERR@\", \"@FN@\"]\n\n\nclass ",1)
body=body.replace("@ERR@",ERR).replace("@FN@",FN)
if "math." in body:
    hdr=hdr.replace("import json\n","import json\nimport math\n",1)
if "ROOT" in body:
    hdr=hdr.replace("import yaml\n","from pathlib import Path\n\nimport yaml\n",1)
    body="ROOT = Path(__file__).resolve().parents[1]\n"+body
open(out,'w').write(hdr+body)
