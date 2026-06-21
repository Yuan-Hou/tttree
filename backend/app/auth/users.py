"""用户清单:DB 表 `users` 为持久真相源,进程内缓存供同步热路径。

种子:首装(表为空)时把 backend/auth_users.toml 的用户哈希后写入 DB(见 bootstrap_and_load_users);
此后以 DB 为准、不再读 toml。变更(建用户 / 改名 / 改密 / 封禁)双写 DB + 刷缓存,本进程即时生效
(封禁立即拦下一次请求)。口令存 bcrypt 哈希(app.auth.passwords)。

热路径 get_user / authenticate / list_users 只读进程缓存(同步、不取 session),让 get_current_user
不变慢。变更类函数才是 async(需 session 写库)。测试用 set_users_for_test 直接注入缓存、绕过 DB。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password, verify_password
from app.db.models import User as UserRow

# app/auth/users.py → parents[2] = backend/
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "auth_users.toml"


@dataclass(frozen=True)
class User:
    id: str
    name: str
    password_hash: str
    is_admin: bool = False
    banned: bool = False


_cache: dict[str, User] = {}


def _to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        name=row.name,
        password_hash=row.password_hash,
        is_admin=bool(row.is_admin),
        banned=bool(row.banned),
    )


# ── 同步热路径(只读缓存)──────────────────────────────────────────────


def get_user(uid: str) -> User | None:
    return _cache.get(uid)


def list_users() -> list[User]:
    """管理控制台用:按 uid 数字序稳定排序(非数字 uid 排末尾)。"""
    return sorted(_cache.values(), key=lambda u: (not u.id.isdigit(), int(u.id) if u.id.isdigit() else 0, u.id))


def name_exists(name: str, *, exclude_uid: str | None = None) -> bool:
    name = name.strip()
    return any(u.name == name and u.id != exclude_uid for u in _cache.values())


def authenticate(name: str, password: str) -> User | None:
    """按登录名 + 口令验证(bcrypt 校验)。被封 / 口令错 → None。"""
    for u in _cache.values():
        if u.name == name:
            if u.banned or not verify_password(password, u.password_hash):
                return None
            return u
    return None


def set_users_for_test(users: dict[str, User]) -> None:
    global _cache
    _cache = dict(users)


# ── 变更(双写 DB + 刷缓存)─────────────────────────────────────────────


def _next_uid() -> str:
    nums = [int(uid) for uid in _cache if uid.isdigit()]
    return str(max(nums) + 1 if nums else 1)


async def create_user(
    session: AsyncSession, name: str, password: str, *, is_admin: bool = False
) -> User:
    uid = _next_uid()
    row = UserRow(
        id=uid,
        name=name.strip(),
        password_hash=hash_password(password),
        is_admin=is_admin,
        banned=False,
    )
    session.add(row)
    await session.commit()
    u = _to_user(row)
    _cache[uid] = u
    return u


async def set_name(session: AsyncSession, uid: str, name: str) -> User | None:
    row = await session.get(UserRow, uid)
    if row is None:
        return None
    row.name = name.strip()
    await session.commit()
    u = _to_user(row)
    _cache[uid] = u
    return u


async def set_password(session: AsyncSession, uid: str, password: str) -> bool:
    row = await session.get(UserRow, uid)
    if row is None:
        return False
    row.password_hash = hash_password(password)
    await session.commit()
    _cache[uid] = _to_user(row)
    return True


async def set_banned(session: AsyncSession, uid: str, banned: bool) -> User | None:
    row = await session.get(UserRow, uid)
    if row is None:
        return None
    row.banned = banned
    await session.commit()
    u = _to_user(row)
    _cache[uid] = u
    return u


# ── 启动:种子 + 载入缓存 ───────────────────────────────────────────────


def _seed_rows_from_toml(path: Path) -> list[UserRow]:
    if not path.exists():
        return []
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[UserRow] = []
    for u in raw.get("users", []):
        uid = str(u.get("id", "")).strip()
        if not uid:
            continue
        name = str(u.get("name") or uid)
        # 管理员:条目显式 admin=true,或老约定的 1 号 / 名为 admin 的种子用户。
        is_admin = bool(u.get("admin")) or uid == "1" or name == "admin"
        out.append(
            UserRow(
                id=uid,
                name=name,
                password_hash=hash_password(str(u.get("password", ""))),
                is_admin=is_admin,
                banned=False,
            )
        )
    return out


def _seed_rows_from_env() -> list[UserRow]:
    """容器首启种子:ADMIN_NAME / ADMIN_PASSWORD 环境变量在(无 toml 时)种出 1 号管理员。
    docker 原生路径——密钥统一走环境,不必把私有 toml 塞进镜像。两者都缺则不种。"""
    import os

    name = (os.environ.get("ADMIN_NAME") or "").strip()
    password = os.environ.get("ADMIN_PASSWORD") or ""
    if not name or not password:
        return []
    return [
        UserRow(id="1", name=name, password_hash=hash_password(password), is_admin=True, banned=False)
    ]


async def bootstrap_and_load_users(session: AsyncSession) -> None:
    """启动调用:DB 空 → 先从 toml 种子,无 toml 再退回 ADMIN_* 环境变量(哈希入库);随后整表载入
    进程缓存。表非空则两者都忽略(DB 为真相源)。"""
    global _cache
    rows = (await session.execute(select(UserRow))).scalars().all()
    if not rows:
        seeds = _seed_rows_from_toml(_DEFAULT_PATH) or _seed_rows_from_env()
        if seeds:
            session.add_all(seeds)
            await session.commit()
            rows = (await session.execute(select(UserRow))).scalars().all()
    _cache = {r.id: _to_user(r) for r in rows}
