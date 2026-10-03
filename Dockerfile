# 公開デモ用（Render など）。本番の社内運用は Windows のインストーラを使う。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_DATA_DIR=/data \
    APP_HOST=0.0.0.0 \
    LOG_TO_FILE=false

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

EXPOSE 8080
# Render は待ち受けるポートを PORT で渡す
CMD ["sh", "-c", "exec python -m app --host 0.0.0.0 --port ${PORT:-8080}"]
