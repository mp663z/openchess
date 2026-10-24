#!/bin/bash
for x in "$@"; do IFS=: read m t <<<"$x"; cd /tmp/wt-$m && MROOT=/tmp/wt-$m/ python3 /tmp/gen2/mut_all.py $m tests/$t > /tmp/fz-$m.txt 2>&1; done
