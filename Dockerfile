FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then \
      npm ci --no-audit --no-fund; \
    else \
      npm install --package-lock-only --no-audit --no-fund && npm ci --no-audit --no-fund; \
    fi
COPY frontend/ .
# 将项目根目录的 VERSION 文件复制到前端构建环境，便于 vite.config.ts 读取
COPY VERSION ./VERSION
RUN npm run build

FROM python:3.11-slim AS backend
ARG TELEGRAM_DEPILER_RELEASE_LABEL=""
ARG TELEGRAM_DEPILER_RELEASE_COMMIT=""
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TELEGRAM_DEPILER_APP_SUPERVISOR=1 \
    TELEGRAM_DEPILER_RELEASE_LABEL=${TELEGRAM_DEPILER_RELEASE_LABEL} \
    TELEGRAM_DEPILER_RELEASE_COMMIT=${TELEGRAM_DEPILER_RELEASE_COMMIT}
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY VERSION ./VERSION
COPY backend/app ./app
COPY docker-entrypoint.sh ./docker-entrypoint.sh
COPY --from=frontend-builder /frontend/dist ./app/static
RUN chmod 755 /app/docker-entrypoint.sh && mkdir -p downloads data
EXPOSE 8000
CMD ["/app/docker-entrypoint.sh"]
