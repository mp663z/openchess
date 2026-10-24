source /tmp/v/bin/activate
cd /home/sandbox/openchess
ruff check tests/test_t0317_retry_integration_restart.py tests/test_t0326_progress_integration_restart.py --output-format concise | tail -1
for x in "retry t0317 t0314_retry_runtime" "progress t0326 t0323_progress_runtime"; do
  set -- $x
  cp tests/test_${2}_${1}_integration_restart.py /tmp/wi-${1}/tests/
  cd /tmp/wi-${1}
  echo "$1 forward: $(timeout 200 python -m pytest -q -p no:cacheprovider tests/test_${2}_${1}_integration_restart.py tests/test_${3}.py 2>&1|tail -1)"
  echo "$1 reverse: $(timeout 200 python -m pytest -q -p no:cacheprovider tests/test_${3}.py tests/test_${2}_${1}_integration_restart.py 2>&1|tail -1)"
  cd /home/sandbox/openchess
done
echo done
