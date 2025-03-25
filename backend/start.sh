#!/bin/sh
redis-server &
rq worker ffmpeg &
rq worker umbrel &
python app.py