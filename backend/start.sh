#!/bin/sh
redis-server redis.conf &
rq worker ffmpeg &
rq worker umbrel &
python app.py