import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import {
  createUser,
  listUsers,
  patchUser,
  resetPassword,
  type AdminUser,
} from "./api";
import { brandTitle } from "./brand";
import type { Session } from "./session";

/** 管理控制台:用户列表 + 建用户 / 改用户名 / 改密码 / 封禁解封。仅管理员可达(后端 /admin 闸再校验)。 */
export function Admin({
  session,
  onBack,
  onLogout,
}: {
  session: Session;
  onBack: () => void;
  onLogout: () => void;
}) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newPw, setNewPw] = useState("");
  const [editUid, setEditUid] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const reload = async () => {
    try {
      setUsers(await listUsers());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submitCreate = (e: FormEvent) => {
    e.preventDefault();
    if (!newName.trim() || !newPw || busy) return;
    run(async () => {
      await createUser(newName.trim(), newPw);
      setNewName("");
      setNewPw("");
    });
  };

  const saveRename = (uid: string) => {
    if (!editName.trim()) return;
    run(async () => {
      await patchUser(uid, { name: editName.trim() });
      setEditUid(null);
    });
  };

  const doResetPw = (u: AdminUser) => {
    const pw = window.prompt(`为「${u.name}」设置新口令:`);
    if (pw == null || pw === "") return;
    run(() => resetPassword(u.id, pw));
  };

  return (
    <div style={S.page}>
      <header style={S.header}>
        <span style={S.brand}>
          <span style={S.dot}>◆</span> {brandTitle()}
        </span>
        <span style={S.title}>管理控制台</span>
        <span style={S.spacer} />
        <span style={S.who}>{session.name}</span>
        <button style={S.ghost} onClick={onBack}>
          返回
        </button>
        <button style={S.ghost} onClick={onLogout}>
          退出
        </button>
      </header>

      <main style={S.main}>
        <form style={S.createBar} onSubmit={submitCreate}>
          <input
            style={S.input}
            placeholder="新用户名"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            style={S.input}
            placeholder="初始口令"
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
          />
          <button
            type="submit"
            style={{ ...S.primary, opacity: busy || !newName.trim() || !newPw ? 0.5 : 1 }}
            disabled={busy || !newName.trim() || !newPw}
          >
            新建用户
          </button>
        </form>

        {err && <div style={S.err}>{err}</div>}

        <table style={S.table}>
          <thead>
            <tr>
              <th style={S.th}>ID</th>
              <th style={S.th}>用户名</th>
              <th style={S.th}>代理用户名</th>
              <th style={S.th}>角色</th>
              <th style={S.th}>状态</th>
              <th style={{ ...S.th, textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const self = u.id === session.uid;
              return (
                <tr key={u.id} style={{ opacity: u.banned ? 0.55 : 1 }}>
                  <td style={S.td}>{u.id}</td>
                  <td style={S.td}>
                    {editUid === u.id ? (
                      <span style={{ display: "inline-flex", gap: 6 }}>
                        <input
                          style={{ ...S.input, padding: "4px 8px" }}
                          value={editName}
                          autoFocus
                          onChange={(e) => setEditName(e.target.value)}
                          onKeyDown={(e) => e.key === "Enter" && saveRename(u.id)}
                        />
                        <button style={S.miniPrimary} onClick={() => saveRename(u.id)}>
                          存
                        </button>
                        <button style={S.mini} onClick={() => setEditUid(null)}>
                          取消
                        </button>
                      </span>
                    ) : (
                      <span style={{ fontWeight: 500 }}>{u.name}</span>
                    )}
                  </td>
                  <td style={S.td}>
                    {u.newapi_username ? (
                      <span style={S.mono}>{u.newapi_username}</span>
                    ) : (
                      <span style={S.muted} title="尚未补齐(用户首次登录时自动建号)">
                        —
                      </span>
                    )}
                  </td>
                  <td style={S.td}>{u.is_admin ? "管理员" : "用户"}</td>
                  <td style={S.td}>
                    {u.banned ? <span style={S.banned}>已封禁</span> : <span style={S.ok}>正常</span>}
                  </td>
                  <td style={{ ...S.td, textAlign: "right", whiteSpace: "nowrap" }}>
                    {editUid !== u.id && (
                      <button
                        style={S.mini}
                        onClick={() => {
                          setEditUid(u.id);
                          setEditName(u.name);
                        }}
                      >
                        改名
                      </button>
                    )}
                    <button style={S.mini} onClick={() => doResetPw(u)}>
                      改密码
                    </button>
                    <button
                      style={{ ...S.mini, color: u.banned ? "#2f8a4e" : "#b4413c", opacity: self ? 0.4 : 1 }}
                      disabled={self || busy}
                      title={self ? "不能封禁自己" : ""}
                      onClick={() => run(() => patchUser(u.id, { banned: !u.banned }))}
                    >
                      {u.banned ? "解封" : "封禁"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </main>
    </div>
  );
}

const S: Record<string, CSSProperties> = {
  page: { minHeight: "100vh", display: "flex", flexDirection: "column" },
  header: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "12px 20px",
    background: "#ffffff",
    borderBottom: "1px solid #e3e6ea",
  },
  brand: { fontSize: 16, fontWeight: 600, color: "#1c2530" },
  dot: { color: "#3b6ea5" },
  title: { fontSize: 14, color: "#7a838e" },
  spacer: { flex: 1 },
  who: { fontSize: 13, color: "#4a5560" },
  ghost: {
    padding: "6px 10px",
    fontSize: 12.5,
    color: "#4a5560",
    background: "#f4f5f7",
    border: "1px solid #e3e6ea",
    borderRadius: 8,
    cursor: "pointer",
  },
  main: { flex: 1, width: "100%", maxWidth: 880, margin: "0 auto", padding: "20px" },
  createBar: { display: "flex", gap: 8, marginBottom: 14 },
  input: {
    padding: "8px 11px",
    fontSize: 13.5,
    border: "1px solid #d4d8dd",
    borderRadius: 8,
    outline: "none",
    background: "#fbfbfc",
    color: "#1c2530",
  },
  primary: {
    padding: "8px 14px",
    fontSize: 13.5,
    fontWeight: 600,
    color: "#ffffff",
    background: "#3b6ea5",
    border: "none",
    borderRadius: 8,
    cursor: "pointer",
    whiteSpace: "nowrap",
  },
  err: {
    fontSize: 12.5,
    color: "#b4413c",
    background: "#fbeceb",
    padding: "8px 10px",
    borderRadius: 8,
    marginBottom: 12,
  },
  table: { width: "100%", borderCollapse: "collapse", background: "#ffffff", border: "1px solid #e3e6ea", borderRadius: 10 },
  th: {
    textAlign: "left",
    fontSize: 11.5,
    fontWeight: 600,
    color: "#7a838e",
    padding: "10px 12px",
    borderBottom: "1px solid #eef0f2",
  },
  td: { fontSize: 13.5, color: "#1c2530", padding: "10px 12px", borderBottom: "1px solid #f1f2f4" },
  mono: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12.5, color: "#4a5560" },
  muted: { color: "#a7adb5" },
  ok: { fontSize: 12, color: "#2f8a4e" },
  banned: { fontSize: 12, color: "#b4413c", fontWeight: 600 },
  mini: {
    marginLeft: 6,
    padding: "4px 9px",
    fontSize: 12,
    color: "#4a5560",
    background: "#f4f5f7",
    border: "1px solid #e3e6ea",
    borderRadius: 7,
    cursor: "pointer",
  },
  miniPrimary: {
    padding: "4px 9px",
    fontSize: 12,
    color: "#ffffff",
    background: "#3b6ea5",
    border: "none",
    borderRadius: 7,
    cursor: "pointer",
  },
};
