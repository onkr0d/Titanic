#!/bin/sh

cleanup() {
    kill -TERM "$ffmpeg_pid" "$umbrel_pid" "$app_pid" 2>/dev/null
    wait "$ffmpeg_pid" "$umbrel_pid"
    wait "$app_pid"
    kill -TERM "$redis_pid" 2>/dev/null
    wait "$redis_pid"
}

trap cleanup TERM INT

# CLI arg, not redis.conf, so the image stays secret-free; the queue holds Firebase refresh tokens
if [ -n "$REDIS_PASSWORD" ]; then
    redis-server redis.conf --requirepass "$REDIS_PASSWORD" &
else
    echo "WARNING: REDIS_PASSWORD is not set — Redis is running without auth" >&2
    redis-server redis.conf &
fi
redis_pid=$!

python worker.py ffmpeg &
ffmpeg_pid=$!

python worker.py umbrel &
umbrel_pid=$!

python app.py &
app_pid=$!

wait "$app_pid"
