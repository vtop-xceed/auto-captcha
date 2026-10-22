#!/bin/sh
set -e
cd "$(dirname "$0")/.."

PORT="${PORT:-3000}"
BASE="http://localhost:$PORT"

echo "Waiting for $BASE/health ..."
i=0
until curl -fsS "$BASE/health" >/dev/null 2>&1; do
  i=$((i + 1))
  [ "$i" -gt 30 ] && { echo "health check failed"; exit 1; }
  sleep 1
done
echo "health OK"

FIXTURE="$(ls data/test/*.jpg | head -1)"
if base64 -w 0 "$FIXTURE" >/dev/null 2>&1; then
  PAYLOAD="$(base64 -w 0 "$FIXTURE")"
else
  PAYLOAD="$(base64 "$FIXTURE" | tr -d '\n')"
fi

RESULT="$(curl -fsS -H 'Content-Type: application/json' \
  -d "{\"image\":\"$PAYLOAD\"}" "$BASE/solve" || true)"

echo "fixture: $(basename "$FIXTURE")"
echo "solve result: $RESULT"

LEN="$(printf %s "$RESULT" | sed -n 's/.*"text":"\([^"]*\)".*/\1/p' | tr -d '\n' | wc -c)"
[ "$LEN" -eq 6 ] || { echo "expected a 6-char solve, got: $RESULT"; exit 1; }
echo "smoke OK"
