#!/bin/sh

cleanup() {
    kill -TERM "$redis_pid" "$ffmpeg_pid" "$umbrel_pid" "$app_pid" 2>/dev/null
    wait "$ffmpeg_pid" "$umbrel_pid"
    wait "$app_pid"
    wait "$redis_pid"
}

trap cleanup TERM INT EXIT

redis-server redis.conf &
redis_pid=$!

rq worker ffmpeg &
ffmpeg_pid=$!

rq worker umbrel &
umbrel_pid=$!

python app.py &
app_pid=$!

wait "$app_pid"
cleanup
