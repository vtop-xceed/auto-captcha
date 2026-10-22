#!/bin/sh

# Start the solver in the background
/app/solver &

# Wait for the socket to be created
while [ ! -S /tmp/captop-solver.sock ]; do
  echo "Waiting for solver socket..."
  sleep 1
done

# Start the API
exec /app/api
