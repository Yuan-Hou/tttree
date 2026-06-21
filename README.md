# Tree

图文小说协同创作台。统一后端(FastAPI)+ 两个前端(创作台 / 登录·管理),Postgres 存储,
LLM 经自建 new-api 网关按用户隔离调用。多前端 · 单后端 · 同源部署。

## 架构

```
反代(你自有,TLS) ──▶ app:8000
                         ├─ /          → 跳 /login/
                         ├─ /login/    登录前端(frontend-auth/dist):登录 + 管理控制台
                         ├─ /app/      创作台前端(frontend/dist)
                         ├─ /auth /admin /story/... API
                         └─ /storage   生成图/参考图
                       postgres(compose 内置)
```

> ⚠️ `/login`、`/app`、`/auth`、`/storage` 必须**同源**(token 经 URL hash 交接)。反代把它们指到同一个 app 即可。
> app **单进程**运行(用户/接入点/key 缓存在进程内存),不要起多 worker;扩展需先外置这些状态。

## 部署(Docker Compose)

### 1. 配置

**仓库根 `.env`**(compose 变量替换用):
```sh
POSTGRES_PASSWORD=改成强随机
ADMIN_NAME=admin           # 首个管理员(仅首启 DB 为空时种子)
ADMIN_PASSWORD=改成强随机
APP_PORT=8000              # 反代指向此端口
```

**`backend/.env`**(应用密钥,见 `backend/.env.example`):
```sh
APP_SECRET=openssl rand -hex 32   # 必填:JWT 签名 + 自填 key 加密,不配无法登录
SITE_NAME=                        # 品牌名「{SITE_NAME} Tree」,留空即「Tree」
NEW_API_BASE_URL=https://api.tttree.online
NEW_API_ADMIN_KEY=                # new-api 站点管理 token(新用户登录时自动建子号+取模型 key)
NEW_API_ADMIN_USER_ID=1
```

### 2. 起服务

```sh
docker compose up -d --build
```

首启自动跑 `alembic upgrade head` 建表、并用 `ADMIN_*` 种出管理员。访问 `http://<host>:<APP_PORT>/`
→ 跳登录页 → 管理员登录后可进创作台或管理控制台。

### 3. (可选)迁移既有 SQLite 数据

把旧 `backend/vore.db` 整库搬进容器的 Postgres(一次性):

```sh
docker compose up -d db
docker compose run --rm --entrypoint python \
  -v "$(pwd)/backend/vore.db:/srv/backend/vore.db:ro" \
  app -m scripts.migrate_sqlite_to_pg --yes
```

### 升级

```sh
git pull && docker compose up -d --build
```

entrypoint 每次启动幂等跑 `alembic upgrade head`,schema 自动跟进;数据卷(`pg-data` / `storage`)保留。

## 本地开发(不走 Docker)

后端默认用 SQLite(`backend/vore.db`),无需 Postgres:

```sh
# 后端(:8000)。需 backend/.env 配 APP_SECRET;用户来自 backend/auth_users.toml(见 .example)
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# 创作台(:5173)、登录·管理(:5174)
cd frontend && npm install && npm run dev
cd frontend-auth && npm install && npm run dev
```

切到 Postgres:设 `DATABASE_URL=postgresql+asyncpg://user:pw@host:5432/db`(留空即回退 SQLite)。

测试:`cd backend && python -m pytest`
