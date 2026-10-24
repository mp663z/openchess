source /tmp/v/bin/activate; cd /tmp/wi-limit
python -m pytest -q -p no:cacheprovider tests/test_t0341_limit_runtime.py tests/test_t0344_limit_integration_restart.py 2>&1|tail -1
python -m pytest -q -p no:cacheprovider tests/test_t0344_limit_integration_restart.py tests/test_t0341_limit_runtime.py 2>&1|tail -1
git bundle create /tmp/lane-b-t0344.bundle build/t0344-limit-integration-restart 2>&1|tail -1; git status --short; git log --oneline -3
