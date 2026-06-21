"""new-api 网关的「管理 + 自助」API 客户端:为某 vore-tree 用户在 new-api 建子用户 + 模型 key。

new-api 没有「管理员替他人建令牌」的接口(建令牌走 UserAuth),故按其源码走六步(已对 v1.0.0-rc.14 核实):
  1. POST /api/user/                建子用户(管理员鉴权:Authorization=admin access token + New-Api-User=admin id)
  2. POST /api/user/login           以该子用户登录 → 拿数字 id + 会话 cookie(httpx 自动持有)
  3. POST /api/token/               以该子用户(cookie + New-Api-User=其 id)建模型令牌(响应不含 key)
  4. GET  /api/token/               列令牌按名字找回刚建那条的 id(列表里 key 打码)
  5. POST /api/token/{id}/key       取明文 key(48 位随机串)
最终模型 key = 'sk-' + key。约束:new-api 用户名 max=20、口令 8–20。

只在「该 vore-tree 用户尚无 new-api 账号」时调用(见 app.newapi.store,幂等)。失败抛 NewApiError,
由上层按「登录照常成功、site 调用降级」处置(不阻断登录)。
"""

import re
import secrets
from dataclasses import dataclass

import httpx

from app.config import settings

_TIMEOUT = 30.0


class NewApiError(RuntimeError):
    """new-api 建号/取 key 失败(站点不可达、鉴权错、额度/配置问题等)。"""


@dataclass(frozen=True)
class ProvisionedAccount:
    newapi_user_id: int
    username: str
    password: str
    token_id: int
    api_key: str  # 'sk-…',直接作 Authorization: Bearer


def is_provisioning_configured() -> bool:
    return bool(settings.new_api_base_url and settings.new_api_admin_key)


def _normalize_brand(name: str) -> str:
    """品牌名规整成用户名安全片段:小写、仅留字母数字(空则空串)。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _gen_username(uid: str) -> str:
    """{规整品牌名}_{uid}_{6位随机}。受 new-api 用户名 20 字符上限约束:优先保住 uid + 完整随机段,
    需要时截断品牌段;极端长 uid 再 [:20] 兜底。品牌空 → 形如 `_{uid}_{rand}`。"""
    rand = secrets.token_hex(3)  # 6 位十六进制
    avail = max(0, 20 - len(uid) - len(rand) - 2)  # 预留两个下划线
    brand = _normalize_brand(settings.site_name)[:avail]
    return f"{brand}_{uid}_{rand}"[:20]


def _gen_password() -> str:
    return secrets.token_urlsafe(12)[:20]  # ~16 字符,落在 new-api 的 8–20 区间


def _admin_headers() -> dict[str, str]:
    return {
        "Authorization": settings.new_api_admin_key or "",
        "New-Api-User": str(settings.new_api_admin_user_id),
    }


def _ok(resp: httpx.Response, what: str) -> dict:
    """new-api 统一信封 {success, message, data}。HTTP 200 但 success=false 也是错。"""
    if resp.status_code != 200:
        raise NewApiError(f"{what}:HTTP {resp.status_code} {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise NewApiError(f"{what}:响应非 JSON {resp.text[:200]}") from exc
    if not body.get("success", False):
        raise NewApiError(f"{what}:{body.get('message') or '未知错误'}")
    return body


async def provision(uid: str) -> ProvisionedAccount:
    if not is_provisioning_configured():
        raise NewApiError("new-api 未配置(缺 base_url 或 admin_key)")
    base = settings.new_api_base_url.rstrip("/")
    username = _gen_username(uid)
    password = _gen_password()
    display = f"vore-{uid}"[:20]

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 1. 建子用户(管理员)
            r = await client.post(
                f"{base}/api/user/",
                headers=_admin_headers(),
                json={"username": username, "password": password, "display_name": display},
            )
            _ok(r, "建 new-api 用户")

            # 2. 以子用户登录 → id + cookie
            r = await client.post(
                f"{base}/api/user/login", json={"username": username, "password": password}
            )
            data = _ok(r, "登录 new-api 子用户").get("data") or {}
            newapi_uid = data.get("id")
            if not isinstance(newapi_uid, int):
                raise NewApiError("登录响应缺 data.id")
            user_headers = {"New-Api-User": str(newapi_uid)}  # 会话 cookie 由 client 自动携带

            # 3. 建模型令牌
            token_name = f"vore-{uid}"[:50]
            r = await client.post(
                f"{base}/api/token/",
                headers=user_headers,
                json={"name": token_name, "unlimited_quota": True, "expired_time": -1},
            )
            _ok(r, "建 new-api 模型令牌")

            # 4. 列令牌找回刚建那条的 id(取同名里 id 最大者)
            r = await client.get(f"{base}/api/token/?p=1&size=100", headers=user_headers)
            listed = _ok(r, "列 new-api 令牌").get("data") or {}
            items = listed.get("items") if isinstance(listed, dict) else listed
            token_id = _pick_token_id(items, token_name)

            # 5. 取明文 key
            r = await client.post(f"{base}/api/token/{token_id}/key", headers=user_headers)
            key = (_ok(r, "取 new-api 令牌 key").get("data") or {}).get("key")
            if not key:
                raise NewApiError("取 key 响应缺 data.key")
    except httpx.HTTPError as exc:  # 连接/超时等网络错 → 也归一为 NewApiError(由上层降级,不阻断登录)
        raise NewApiError(f"new-api 网络错误:{exc}") from exc

    return ProvisionedAccount(
        newapi_user_id=newapi_uid,
        username=username,
        password=password,
        token_id=token_id,
        api_key=f"sk-{key}",
    )


async def get_user_quota(newapi_user_id: int) -> dict:
    """查某 new-api 子用户的额度(管理员 GET /api/user/{id})。返回 {quota(剩余), used_quota}。"""
    if not is_provisioning_configured():
        raise NewApiError("new-api 未配置(缺 base_url 或 admin_key)")
    base = settings.new_api_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{base}/api/user/{newapi_user_id}", headers=_admin_headers())
            data = _ok(r, "查 new-api 用户额度").get("data") or {}
    except httpx.HTTPError as exc:
        raise NewApiError(f"new-api 网络错误:{exc}") from exc
    return {"quota": int(data.get("quota") or 0), "used_quota": int(data.get("used_quota") or 0)}


def _pick_token_id(items, token_name: str) -> int:
    if not isinstance(items, list):
        raise NewApiError("列令牌响应结构异常")
    matches = [it for it in items if isinstance(it, dict) and it.get("name") == token_name]
    if not matches:
        raise NewApiError(f"列表里找不到刚建的令牌 {token_name!r}")
    return max(int(it["id"]) for it in matches)
