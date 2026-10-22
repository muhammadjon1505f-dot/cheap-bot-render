FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV DB_FILE=/app/smm_bot.db
ENV PORT=10000
ENV TZ=Asia/Tashkent

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Tashkent /etc/localtime \
    && echo "Asia/Tashkent" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["sh", "-c", "python3 -m http.server ${PORT:-10000} & python3 cheap.py"]
