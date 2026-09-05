#!/bin/sh
set -eu

workspace=${ANY_HARNESS_EVAL_WORKSPACE:?missing evaluation workspace}
test ! -e "$workspace/REVIEW.md"
grep -F 'return int(left) / int(right)' "$workspace/src/parse.py" >/dev/null
test -f "$workspace/tests/test_parse.py"
