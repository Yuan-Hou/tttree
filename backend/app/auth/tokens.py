"""登录令牌:复用 .env 的 APP_SECRET 作 HMAC 密钥签发 JWT(长期有效,无过期)。

载荷只含 uid。无服务端 session 表 → 无状态、契合多前端/单后端;登出 = 前端丢弃 token。
APP_SECRET 未配置 → 无法签发/校验,抛 AuthConfigError(由上层转 503,与「口令错」401 区分)。

模块命名 tokens 而非 jwt:避免与 PyJWT 顶层包 `jwt` 同名混淆。
"""

import jwt

from app.config import settings

_ALG = "HS256"


class AuthConfigError(RuntimeError):
    """服务端未配置 APP_SECRET,登录子系统不可用。"""


def _secret() -> str:
    if not settings.app_secret:
        raise AuthConfigError("APP_SECRET 未配置,无法签发/校验登录令牌")
    return settings.app_secret


def make_token(uid: str) -> str:
    return jwt.encode({"uid": uid}, _secret(), algorithm=_ALG)


def decode_uid(token: str) -> str | None:
    """校验签名并取 uid。签名/格式无效 → None;APP_SECRET 缺失 → 抛 AuthConfigError。"""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    uid = payload.get("uid")
    return str(uid) if uid is not None else None
