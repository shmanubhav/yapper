#!/usr/bin/env bash
# CI tests: every tests/test_*.py with the runner's python3, as AGENTS.md runs them; any failing file fails the job.
set -uo pipefail
cd "$(dirname "$0")/.."
python3 --version
rc=0
for t in tests/test_*.py; do
  echo "== $t"
  python3 -I "$t" || { echo "FAIL $t"; rc=1; }
done
exit $rc
