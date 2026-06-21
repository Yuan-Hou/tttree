"""每用户接入点供应商配置的读写。

落库形状见 db.models.AppSettings(每用户一行,主键 = user_id)。职责:
- get-or-create 某用户的行;
- 把库里的 custom 配置解密成内存覆盖表(app.llm.endpoints),按用户分隔,供 registry 取用;
- 给前端的公开载荷(public_payload):**绝不**回传明文 / 密文,只给「是否已设 key + 掩码」;
- 更新:site 模式清掉该接入点的 key;custom 模式加密新 key(未传 key 则保留旧密文,允许只改 URL)。
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crypto
from app.config import settings
from app.db.models import AppSettings
from app.llm.endpoints import (
    ENDPOINTS,
    Override,
    clear_all_overrides,
    get_endpoint,
    has_site_key,
    is_known_endpoint,
    new_api_base_for,
    set_user_overrides,
)


async def get_app_settings(session: AsyncSession, user_id: str) -> AppSettings:
    row = await session.get(AppSettings, user_id)
    if row is None:
        row = AppSettings(id=user_id, endpoints_json="{}")
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _parse(row: AppSettings) -> dict[str, dict]:
    try:
        data = json.loads(row.endpoints_json or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def row_to_overrides(row: AppSettings) -> dict[str, Override]:
    """库行 → 内存覆盖表(仅 custom 且可解密的接入点)。解不开的项静默回落本站点服务。"""
    out: dict[str, Override] = {}
    for eid, cfg in _parse(row).items():
        if not is_known_endpoint(eid) or not isinstance(cfg, dict):
            continue
        if cfg.get("mode") != "custom":
            continue
        base_url = (cfg.get("base_url") or "").strip()
        enc = cfg.get("api_key_enc") or ""
        if not base_url or not enc:
            continue
        try:
            key = crypto.decrypt(enc)
        except crypto.CryptoUnavailable:
            continue  # APP_SECRET 缺失/变更 → 该接入点回落本站点服务
        out[eid] = Override(base_url=base_url, api_key=key)
    return out


async def load_overrides_into_memory(session: AsyncSession) -> None:
    """启动时调用:把库里**所有用户**的 custom 配置逐用户载入内存覆盖表。"""
    clear_all_overrides()
    rows = (await session.execute(select(AppSettings))).scalars().all()
    for row in rows:
        set_user_overrides(row.id, row_to_overrides(row))


def _mask(enc: str) -> str:
    """密文 → 掩码(尽力解密只露尾 4 位);解不开则给中性提示。绝不回传完整 key。"""
    try:
        key = crypto.decrypt(enc)
    except crypto.CryptoUnavailable:
        return "（已设置 · 无法解密）"
    tail = key[-4:] if len(key) >= 4 else key
    return f"••••{tail}"


def public_payload(row: AppSettings) -> dict:
    """前端渲染全局设置用。逐接入点给目录信息 + 当前模式 / URL / key 是否已设 + 掩码。"""
    stored = _parse(row)
    endpoints = []
    for ep in ENDPOINTS:
        cfg = stored.get(ep.id) if isinstance(stored.get(ep.id), dict) else {}
        mode = "custom" if cfg.get("mode") == "custom" else "site"
        enc = cfg.get("api_key_enc") or ""
        endpoints.append({
            "id": ep.id,
            "label": ep.label,
            "group": ep.group,
            "mode": mode,
            "site_base_url": ep.site_base_url,  # 官方默认 URL(仅作自定义模式占位/候选)
            "site_effective_base": new_api_base_for(ep.id),  # 本站点服务实际经 new-api 的 URL
            "presets": list(ep.presets),
            # custom 时回显已存 URL;site 时给官方默认作占位
            "base_url": (cfg.get("base_url") or ep.site_base_url),
            "key_set": bool(enc),
            "key_masked": _mask(enc) if enc else "",
        })
    # 本站点服务现经 new-api 网关、按用户独立 token。new_api_ready=该用户的 token 是否已就绪(否则 site 调用会报错)。
    return {
        "crypto_available": crypto.is_available(),
        "new_api_base_url": settings.new_api_base_url,
        "new_api_ready": has_site_key(row.id),
        "endpoints": endpoints,
    }


class GlobalSettingsError(ValueError):
    """更新校验失败(未知接入点 / custom 缺 URL 或 key / APP_SECRET 未配置)。"""


async def update_app_settings(
    session: AsyncSession, user_id: str, updates: dict[str, dict]
) -> AppSettings:
    """合并更新某用户的接入点配置。updates: {endpoint_id: {mode, base_url?, api_key?}}。

    - mode='site':清掉该接入点的自定义(回落 .env);
    - mode='custom':需 base_url;api_key 传了非空 → 加密存,未传 → 保留旧密文(允许只改 URL);
      首次设 custom 又没给 key → 报错。APP_SECRET 未配置而要存 key → 报错。
    只改 updates 里出现的接入点,其余不动。更新后即时刷新该用户的内存覆盖表。
    """
    row = await get_app_settings(session, user_id)
    stored = _parse(row)

    for eid, change in updates.items():
        if not is_known_endpoint(eid):
            raise GlobalSettingsError(f"未知接入点:{eid!r}")
        if not isinstance(change, dict):
            raise GlobalSettingsError(f"接入点 {eid!r} 配置格式错误")
        mode = change.get("mode")
        if mode == "site":
            stored.pop(eid, None)  # 回落本站点服务
            continue
        if mode != "custom":
            raise GlobalSettingsError(f"接入点 {eid!r} 的 mode 须为 'site' 或 'custom'")

        base_url = (change.get("base_url") or "").strip()
        if not base_url:
            raise GlobalSettingsError(f"接入点 {eid!r} 自定义模式需填写 endpoint")
        prev = stored.get(eid) if isinstance(stored.get(eid), dict) else {}
        new_key = change.get("api_key")
        if new_key:  # 传了新 key → 加密
            if not crypto.is_available():
                raise GlobalSettingsError("APP_SECRET 未配置,无法保存自填 API key")
            enc = crypto.encrypt(new_key.strip())
        else:  # 未传 → 沿用旧密文
            enc = prev.get("api_key_enc") or ""
            if not enc:
                raise GlobalSettingsError(f"接入点 {eid!r} 自定义模式需提供 API key")
        stored[eid] = {"mode": "custom", "base_url": base_url, "api_key_enc": enc}

    row.endpoints_json = json.dumps(stored, ensure_ascii=False)
    await session.commit()
    await session.refresh(row)
    set_user_overrides(user_id, row_to_overrides(row))  # 即时生效(仅该用户)
    return row
