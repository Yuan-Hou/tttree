"""测试夹具:鉴权环境 + 默认放行。

加了用户系统后,所有故事级路由都要 Bearer token。既有 API 测试不带 token 会一律 401。为免逐个
改造,这里默认把 get_current_user 覆盖成「1 号用户」(故事默认归属也是 "1",归属校验自然通过)。

需要验证真实鉴权 / 跨用户隔离的模块,在文件顶部写 `pytestmark = pytest.mark.real_auth`,
本夹具便跳过覆盖,让它们自带 token 走真链路。
"""

import pytest

from app.auth import users as users_mod
from app.auth.context import current_uid
from app.auth.passwords import hash_password
from app.auth.users import User

# 全套测试共用的固定用户(缓存里存哈希;1 号是管理员)。
_TEST_USERS = {
    "1": User("1", "admin", hash_password("pw-admin"), is_admin=True),
    "2": User("2", "bob", hash_password("pw-bob")),
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_auth: 模块自验真实鉴权,夹具不自动覆盖 get_current_user(默认覆盖成 1 号用户)。",
    )


@pytest.fixture(autouse=True)
def _auth_env(request, monkeypatch):
    monkeypatch.setattr("app.config.settings.app_secret", "test-secret-please-change", raising=False)
    # 关闭真实 new-api 建号:避免任何用例(尤其登录钩子)向真站点发请求。test_newapi 自行 mock 重开。
    monkeypatch.setattr("app.config.settings.new_api_admin_key", None, raising=False)
    users_mod.set_users_for_test(dict(_TEST_USERS))
    # 为 1 号用户预置一个「本站点服务」模型 key,让走到 resolve_endpoint 本站点服务分支的用例拿得到 key。
    from app.llm import endpoints as _ep

    _ep.set_user_site_key("1", "sk-test-site")

    try:
        if request.node.get_closest_marker("real_auth"):
            yield
            return

        from app.main import app
        from app.web.auth_deps import get_current_user, require_story_owner

        def _fake_user() -> str:
            current_uid.set("1")
            return "1"

        def _fake_owner(story_id: str) -> str:
            # 既有 draw/turn 测试只 monkeypatch 各自路由模块的 async_session,不覆盖 get_session 的来源。
            # 归属闸经 get_session 查库会落到未迁移的真实 vore.db。这些用例本不测归属(归属由
            # test_isolation 走真链路覆盖),故放行:回传路径里的 story_id,不查库。
            current_uid.set("1")
            return story_id

        app.dependency_overrides[get_current_user] = _fake_user
        app.dependency_overrides[require_story_owner] = _fake_owner
        try:
            yield
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(require_story_owner, None)
    finally:
        _ep.clear_all_site_keys()
