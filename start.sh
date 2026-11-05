#!/bin/bash
export TZ="Asia/Tashkent"
export PYTHONUNBUFFERED=1
export PORT="${PORT:-10000}"

echo "=================================================="
echo "🚀 RENDER MULTI-BOT SYSTEM ISHGA TUSHMOQDA..."
echo "=================================================="

# 1. HTTP health check server for Render (Render uyquga ketmasligi uchun)
python3 -m http.server "$PORT" &

# 2. Cheap SMM Bot (Avtomatik qayta tiklanuvchi fon jarayoni)
(
  while true; do
    echo "🤖 [SMM BOT] Ishga tushirilmoqda..."
    python3 cheap.py || true
    echo "⚠️ [SMM BOT] To'xtadi. 5 soniyadan so'ng qayta ishga tushadi..."
    sleep 5
  done
) &

# 3. Epic Rocket Signal Bot & 24/7 Jonli Efir (Avtomatik qayta tiklanuvchi fon jarayoni)
(
  while true; do
    echo "🚀 [ROCKET BOT] Ishga tushirilmoqda..."
    python3 rocket_bot/main_service.py || true
    echo "⚠️ [ROCKET BOT] To'xtadi. 5 soniyadan so'ng qayta ishga tushadi..."
    sleep 5
  done
) &

# Asosiy konteynerni doimiy uyg'oq saqlash
while true; do
  sleep 3600
done
