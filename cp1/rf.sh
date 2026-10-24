source /tmp/v/bin/activate; cd /tmp/wi-limit
for m in 's/gained = (now - prev\["updated_at"\]) \/\/ refill/gained = (now - prev["updated_at"]) \/\/ refill + 1/' 's/prev\["updated_at"\] + gained \* refill/prev["updated_at"] + gained * refill + 1/' 's/refilled = prev\["tokens"\] + gained, prev/refilled = prev["tokens"] + gained - 1, prev/' 's/- (now - base)$/- (now - base) + 1/'; do
 git checkout -q server/; sed -i "$m" server/jobs_limit.py; git diff --stat server|tail -1
 python -m pytest -q -x -p no:cacheprovider tests/test_t0344_limit_integration_restart.py -k "small_bucket" 2>&1|tail -1
done; git checkout -q server/
