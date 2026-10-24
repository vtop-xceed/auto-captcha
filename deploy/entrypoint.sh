#!/bin/sh

# solver supervisor: stays alive across exec api, restarts on crash so a dead
# solver is a ~2s blip instead of permanent 503s
(
  while :; do
    /app/solver
    echo "SOLVER DIED (exit $?), restarting in 2s"
    sleep 2
  done
) &

n=0
while [ ! -S /tmp/captop-solver.sock ] && [ "$n" -lt 30 ]; do
  echo "Waiting for solver socket..."
  sleep 1
  n=$((n + 1))
done

exec /app/api
