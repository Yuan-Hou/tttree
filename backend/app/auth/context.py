"""请求级身份:当前登录用户号(uid)。

用 contextvars 携带,让深处的凭证解析(app.llm.endpoints.resolve_endpoint)在不逐层穿参的前提下
拿到「这次调用属于哪个用户」。在鉴权依赖 get_current_user 里 set;无 ctx(单元测试 / 非请求路径)
默认 None,resolve_endpoint 回落本站点服务(等价于一个全用本站 key 的用户)。

asyncio.create_task 会复制创建时的上下文快照 → 并行子任务(如 B∥Options)天然继承同一 uid。
"""

from contextvars import ContextVar

current_uid: ContextVar[str | None] = ContextVar("current_uid", default=None)


def get_current_uid() -> str | None:
    return current_uid.get()
