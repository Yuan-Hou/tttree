#!/bin/sh
set -e

# DB schema 跑到最新(幂等)。DATABASE_URL 由 compose 指向 postgres 服务;首启即建全表。
echo "[entrypoint] alembic upgrade head …"
alembic upgrade head

# 单进程铁律:用户缓存 / 接入点覆盖表 / site key 都是进程内内存 → 只能 1 个 worker、1 个进程,
# 否则各 worker 各持一份缓存、管理员改动只在某个 worker 生效。横向扩展需先把这些状态外置。
echo "[entrypoint] starting uvicorn (single worker) …"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
