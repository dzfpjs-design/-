#!/bin/sh
# Linux-only integration check. Supply a freshly compiled bridge and the official
# SDK jar as BRIDGE_TEST_CLASSPATH. No network or credentials are needed.
set -eu
: "${BRIDGE_TEST_CLASSPATH:?Set BRIDGE_TEST_CLASSPATH to compiled classes and SDK jar}"
test_dir=$(mktemp -d)
holder=''
supervisor=''
child=''
cleanup() {
  [ -z "$supervisor" ] || kill -9 "$supervisor" 2>/dev/null || true
  [ -z "$holder" ] || kill "$holder" 2>/dev/null || true
  [ -z "$child" ] || kill -9 "$child" 2>/dev/null || true
  rm -rf "$test_dir"
}
trap cleanup EXIT HUP INT TERM
mkfifo "$test_dir/in"
(sleep 30 > "$test_dir/in") & holder=$!
sh -c '
  java -classpath "$BRIDGE_TEST_CLASSPATH" DjiCloud2Bridge <"$1/in" >/dev/null 2>&1 &
  echo $! > "$1/pid"
  while :; do sleep 1; done
' sh "$test_dir" & supervisor=$!
tries=0
while [ ! -s "$test_dir/pid" ] && [ "$tries" -lt 50 ]; do sleep 0.1; tries=$((tries+1)); done
child=$(cat "$test_dir/pid")
sleep 1
kill -0 "$child"
kill -9 "$supervisor"
supervisor=''
sleep 2
if [ -e "/proc/$child/status" ] && ! grep -q '^State:.*Z' "/proc/$child/status"; then
  echo 'parent-death watchdog failed' >&2
  exit 1
fi
# Do not signal a now-reused PID during cleanup.
child=''
echo 'parent-death watchdog passed'
