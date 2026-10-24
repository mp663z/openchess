set -e
cd /home/sandbox/openchess
source /tmp/v/bin/activate
F=$(ls tests/test_t03{17,26,35,44,53}_*integration_restart.py)
ruff check --fix -q $F || true
ruff format -q $F
ruff check $F | tail -1
B=b91b689607a80df00b01886356218347633571d3
for x in "t0317 retry" "t0326 progress" "t0335 dead_letter" "t0344 limit" "t0353 restart"; do
  set -- $x
  tk=${1}; md=${2}
  git worktree add -q -b build/${tk}-${md//_/-}-integration-restart /tmp/wi-${md} $B
  cp tests/test_${tk}_${md}_integration_restart.py /tmp/wi-${md}/tests/
  (cd /tmp/wi-${md} && git add tests && git -c user.name="Instinct Agent" -c user.email=agent@instinct.com commit -qm "${tk}: integration and restart battery for jobs ${md} (tests only, docs follow)" && git rev-parse --short HEAD)
done
