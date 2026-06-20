"""全局设置里「自填供应商 API key」落库前的对称加密(Fernet)。

key = base64url(sha256(APP_SECRET));APP_SECRET 来自 .env。未配置 APP_SECRET → 自填 key 功能不可用
(全局设置只能用「本站点服务」),encrypt/decrypt 抛 CryptoUnavailable,上层据此返回友好错误。

密文带版本前缀 'f1:',便于日后换算法平滑迁移。decrypt 对「APP_SECRET 变更 / 密文损坏」一并归为
CryptoUnavailable —— 读设置时遇到即把该接入点回落到本站点服务,不让整库读取崩。
"""

import base64
import hashlib

from app.config import settings

_PREFIX = "f1:"


class CryptoUnavailable(RuntimeError):
    """APP_SECRET 未配置,或密文无法用当前 APP_SECRET 解开。"""


def is_available() -> bool:
    """APP_SECRET 是否已配置(决定全局设置能否启用「自填 key」)。"""
    return bool(settings.app_secret)


def _fernet():
    from cryptography.fernet import Fernet

    if not settings.app_secret:
        raise CryptoUnavailable("APP_SECRET 未配置:无法加解密自填 API key")
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """明文 key → 带前缀密文。APP_SECRET 缺失则抛 CryptoUnavailable。"""
    token = _fernet().encrypt(plaintext.encode()).decode()
    return _PREFIX + token


def decrypt(ciphertext: str) -> str:
    """带前缀密文 → 明文 key。格式不识别 / APP_SECRET 变更 / 损坏 → CryptoUnavailable。"""
    from cryptography.fernet import InvalidToken

    if not ciphertext.startswith(_PREFIX):
        raise CryptoUnavailable("密文格式不识别")
    try:
        return _fernet().decrypt(ciphertext[len(_PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise CryptoUnavailable("解密失败:APP_SECRET 可能已变更或密文损坏") from exc
