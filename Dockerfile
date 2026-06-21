# syntax=docker/dockerfile:1
# 单镜像:Node 阶段构建两个前端 → Python 阶段跑 FastAPI,同源伺服 API + /app + /login + /storage。
# 构建上下文 = 仓库根(能取到 backend/ frontend/ frontend-auth/)。

# ---- Stage 1:构建两个前端 ----
FROM node:20-slim AS web
WORKDIR /build

# 先装依赖(利用缓存:lock 不变就不重装)
COPY frontend/package.json frontend/package-lock.json frontend/
RUN cd frontend && npm ci
COPY frontend-auth/package.json frontend-auth/package-lock.json frontend-auth/
RUN cd frontend-auth && npm ci

# 再拷源码构建。frontend 的 build 同时产出 dist + dist-viewer(导出查看器,后端运行时要读)。
COPY frontend/ frontend/
RUN cd frontend && npm run build
COPY frontend-auth/ frontend-auth/
RUN cd frontend-auth && npm run build

# ---- Stage 2:运行时 ----
FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

# 后端代码(.dockerignore 已排除 .venv / *.db / storage / auth_users.toml / .env)
COPY backend/ /srv/backend/

# 前端产物按 backend 的同级目录摆放,让后端硬编码路径(parents[2]/frontend…)直接解析。
COPY --from=web /build/frontend/dist /srv/frontend/dist
COPY --from=web /build/frontend/dist-viewer /srv/frontend/dist-viewer
COPY --from=web /build/frontend-auth/dist /srv/frontend-auth/dist

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
