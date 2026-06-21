// 登录项目自己的会话(与创作台 localStorage 分开 key,互不污染)。管理员可在本项目内停留
// (落地选择 / 管理控制台),故 token 落本地;调 /admin/* 时自动带 Bearer。普通用户登录后立刻
// hash 交接到创作台,不在此停留。

export interface Session {
  token: string;
  uid: string;
  name: string;
  is_admin: boolean;
}

const TOKEN = "vauth_token";
const UID = "vauth_uid";
const NAME = "vauth_name";
const ADMIN = "vauth_is_admin";

export function loadSession(): Session | null {
  const token = localStorage.getItem(TOKEN);
  const uid = localStorage.getItem(UID);
  if (!token || !uid) return null;
  return {
    token,
    uid,
    name: localStorage.getItem(NAME) || uid,
    is_admin: localStorage.getItem(ADMIN) === "1",
  };
}

export function saveSession(s: Session): void {
  localStorage.setItem(TOKEN, s.token);
  localStorage.setItem(UID, s.uid);
  localStorage.setItem(NAME, s.name);
  localStorage.setItem(ADMIN, s.is_admin ? "1" : "0");
}

export function clearSession(): void {
  [TOKEN, UID, NAME, ADMIN].forEach((k) => localStorage.removeItem(k));
}

// 创作台地址:开发期默认 :5173,部署期默认同源 /app/。可用 VITE_APP_URL 覆盖。
const APP_URL: string =
  import.meta.env.VITE_APP_URL || (import.meta.env.DEV ? "http://localhost:5173/" : "/app/");

/** 交接到创作台:token/uid/name 放 hash(创作台启动时捕获 → localStorage)。 */
export function redirectToApp(s: Pick<Session, "token" | "uid" | "name">): void {
  window.location.href =
    `${APP_URL}#token=${encodeURIComponent(s.token)}` +
    `&uid=${encodeURIComponent(s.uid)}&name=${encodeURIComponent(s.name)}`;
}

/** 带 Bearer 的 fetch(供 /admin/*)。401 → 抛出,由调用方提示重登。 */
export async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = localStorage.getItem(TOKEN);
  const headers = new Headers(init?.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}
