#!/bin/bash
export PYTHONUNBUFFERED=1
export PORT="${PORT:-3000}"

echo "Starting HTTP ping server on port $PORT..."
python3 -m http.server "$PORT" &

echo "Starting Telegram Bot..."
python3 cheap.py
