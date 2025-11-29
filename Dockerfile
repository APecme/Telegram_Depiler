FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
# 将项目根目录的 VERSION 文件复制到前端构建环境，便于 vite.config.ts 读取
COPY VERSION ./VERSION
RUN npm run build

FROM python:3.11-slim AS backend
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY VERSION ./VERSION
COPY backend/app ./app
COPY --from=frontend-builder /frontend/dist ./app/static
RUN mkdir -p downloads data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


