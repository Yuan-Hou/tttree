"""用户清单:无注册系统,用户写死在配置文件 backend/auth_users.toml(gitignored)。

文件形如:
    [[users]]
    id = "1"        # 数据归属用户号(= Story.owner_id);也是 JWT 载荷里的 uid
    name = "admin"  # 登录名
    password = "…"  # 登录口令(明文;自托管、文件不入库)

懒加载 + 进程内缓存。文件缺失 → 空清单(任何登录都失败),不做匿名兜底。
测试用 set_users_for_test 直接注入。新增用户 = 改文件后重启进程。
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

# app/auth/users.py → parents[2] = backend/
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "auth_users.toml"


@dataclass(frozen=True)
class User:
    id: str
    name: str
    password: str


_cache: dict[str, User] | None = None


def _read(path: Path) -> dict[str, User]:
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, User] = {}
    for u in raw.get("users", []):
        uid = str(u.get("id", "")).strip()
        if not uid:
            continue
        out[uid] = User(id=uid, name=str(u.get("name") or uid), password=str(u.get("password", "")))
    return out


def _users() -> dict[str, User]:
    global _cache
    if _cache is None:
        _cache = _read(_DEFAULT_PATH)
    return _cache


def reload_users(path: Path | None = None) -> dict[str, User]:
    """强制重载(测试 / 运维改了文件后)。返回新清单。"""
    global _cache
    _cache = _read(path or _DEFAULT_PATH)
    return _cache


def set_users_for_test(users: dict[str, User]) -> None:
    global _cache
    _cache = dict(users)


def get_user(uid: str) -> User | None:
    return _users().get(uid)


def authenticate(name: str, password: str) -> User | None:
    """按登录名 + 口令验证(明文比对)。命中返回 User,否则 None。"""
    for u in _users().values():
        if u.name == name and u.password == password:
            return u
    return None
