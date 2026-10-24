#!/bin/bash
cd /home/sandbox/openchess
while read -r name branch mod red rt; do
  ( cd /tmp/wt-$name && MROOT=/tmp/wt-$name/ setsid nohup /tmp/v/bin/python /tmp/gen2/mut.py $mod tests/$red tests/$rt > /tmp/mutout-$name.txt 2>&1 & )
done <<'LIST'
retry t0314-retry-implement jobs_retry test_t0313_retry_red.py test_t0314_retry_runtime.py
progress t0323-progress-implement jobs_progress test_t0322_progress_red.py test_t0323_progress_runtime.py
dl t0332-dead-letter-implement jobs_dead_letter test_t0331_dead_letter_red.py test_t0332_dead_letter_runtime.py
limit t0341-limit-implement jobs_limit test_t0340_limit_red.py test_t0341_limit_runtime.py
restart t0350-restart-implement jobs_restart test_t0349_restart_red.py test_t0350_restart_runtime.py
LIST
