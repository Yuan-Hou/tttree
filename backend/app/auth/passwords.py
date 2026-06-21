"""口令哈希:bcrypt(自托管,无需 passlib)。建用户 / 改密码时哈希落库,登录时校验。

bcrypt 只取口令前 72 字节(其算法上限),这里显式截断以免超长口令在某些版本报错。
校验对任何异常(哈希格式损坏、空哈希等)一律判否,绝不抛到调用方。
"""

import bcrypt

_MAX = 72  # bcrypt 的口令字节上限


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:_MAX]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX], hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
