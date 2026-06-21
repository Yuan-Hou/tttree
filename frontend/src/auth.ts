// 登录会话 + 鉴权接线。对话前端**硬要求登录**:无 token / token 失效 → 跳登录前端,无匿名兜底。
//
// 交接:登录前端登录成功后重定向到本应用 URL,token/uid 放在 hash(#token=…&uid=…)。这里在启动时
// 捕获并落 localStorage,随即清掉 URL 里的 token。之后所有同源请求由 fetch 垫片自动带上 Bearer,
// 任一响应 401 → 清会话跳登录(口令在配置文件里轮换、或令牌签名密钥变更等都会走到这)。

const TOKEN_KEY = "vore_token";
const UID_KEY = "vore_uid";
const NAME_KEY = "vore_name";

export const getToken = (): string | null => localStorage.getItem(TOKEN_KEY);
export const getUid = (): string | null => localStorage.getItem(UID_KEY);
/** 登录名(交接时带上)。老会话未存 → null,调用方回退到 uid。 */
export const getName = (): string | null => localStorage.getItem(NAME_KEY);

function setSession(token: string, uid: string, name: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(UID_KEY, uid);
  if (name) localStorage.setItem(NAME_KEY, name);
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(UID_KEY);
  localStorage.removeItem(NAME_KEY);
}

// 登录前端地址:开发期默认 :5174(独立 Vite),部署期默认同源 /login/。可用 VITE_LOGIN_URL 覆盖。
const LOGIN_URL: string =
  import.meta.env.VITE_LOGIN_URL || (import.meta.env.DEV ? "http://localhost:5174/" : "/login/");

export function redirectToLogin(): void {
  clearSession();
  window.location.href = LOGIN_URL;
}

export function logout(): void {
  redirectToLogin();
}

/** 仅对同源(相对路径或同 origin)请求带 token / 处置 401;不碰跨域请求。 */
function isSameOrigin(input: RequestInfo | URL): boolean {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return url.startsWith("/") || url.startsWith(window.location.origin);
}

let installed = false;

/** 安装 fetch 垫片:同源请求自动加 Authorization;响应 401 → 跳登录。幂等。 */
export function installAuthFetch(): void {
  if (installed) return;
  installed = true;
  const real = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const opts = init ?? {};
    let nextInit: RequestInit = opts;
    const token = getToken();
    if (token && isSameOrigin(input)) {
      const headers = new Headers(opts.headers || {});
      headers.set("Authorization", `Bearer ${token}`);
      nextInit = { ...opts, headers };
    }
    const resp = await real(input, nextInit);
    if (resp.status === 401 && isSameOrigin(input)) {
      redirectToLogin();
    }
    return resp;
  };
}

/** 启动引导:捕获交接 token → 落库 → 清 URL。无 token 则跳登录返回 false(调用方不渲染应用)。 */
export function bootstrapAuth(): boolean {
  if (window.location.hash.startsWith("#token=")) {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("token");
    const uid = params.get("uid");
    if (token && uid) {
      setSession(token, uid, params.get("name") || "");
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }
  if (!getToken()) {
    redirectToLogin();
    return false;
  }
  installAuthFetch();
  return true;
}
