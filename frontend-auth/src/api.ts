// 登录 + 管理控制台 API(同源,经 Vite proxy 转 :8000)。
import { authFetch } from "./session";

export interface LoginResp {
  token: string;
  uid: string;
  name: string;
  is_admin: boolean;
}

export interface AdminUser {
  id: string;
  name: string;
  is_admin: boolean;
  banned: boolean;
  created_at?: string | null;
}

async function detail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    return j?.detail || `${r.status}`;
  } catch {
    return `${r.status}`;
  }
}

export async function login(name: string, password: string): Promise<LoginResp> {
  const r = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, password }),
  });
  if (!r.ok) throw new Error(r.status === 401 ? "用户名或口令错误" : `登录失败(${r.status})`);
  return r.json();
}

export async function listUsers(): Promise<AdminUser[]> {
  const r = await authFetch("/admin/users");
  if (!r.ok) throw new Error(await detail(r));
  return r.json();
}

export async function createUser(name: string, password: string): Promise<AdminUser> {
  const r = await authFetch("/admin/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, password }),
  });
  if (!r.ok) throw new Error(await detail(r));
  return r.json();
}

export async function patchUser(
  uid: string,
  patch: { name?: string; banned?: boolean },
): Promise<AdminUser> {
  const r = await authFetch(`/admin/users/${uid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(await detail(r));
  return r.json();
}

export async function resetPassword(uid: string, newPassword: string): Promise<void> {
  const r = await authFetch(`/admin/users/${uid}/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (!r.ok) throw new Error(await detail(r));
}
