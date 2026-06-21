import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { login as apiLogin, type LoginResp } from "./api";

/** 登录页。登录成功后把 {token, uid, name, is_admin} 上抛 App,由 App 决定落地(创作台 / 管理控制台)。 */
export function Login({ onLoggedIn }: { onLoggedIn: (resp: LoginResp) => void }) {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // 品牌名「{Name} Tree」(留空 → 仅「Tree」):Name 来自后端 /brand(SITE_NAME),同步后本地缓存。
  const [brand, setBrand] = useState(localStorage.getItem("vore_brand_name") || "");
  const brandTitle = brand.trim() ? `${brand.trim()} Tree` : "Tree";
  useEffect(() => {
    fetch("/brand")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d?.name) {
          localStorage.setItem("vore_brand_name", d.name);
          setBrand(d.name);
        }
      })
      .catch(() => {});
  }, []);
  useEffect(() => {
    document.title = `登录 · ${brandTitle}`;
  }, [brandTitle]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setErr(null);
    try {
      onLoggedIn(await apiLogin(name.trim(), password));
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : `无法连接服务:${String(e2)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={S.wrap}>
      <form style={S.card} onSubmit={submit}>
        <div style={S.brand}>
          <span style={S.dot}>◆</span> {brandTitle}
        </div>
        <div style={S.sub}>登录以继续</div>

        <label style={S.label}>
          <span style={S.labelText}>用户名</span>
          <input
            style={S.input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>
        <label style={S.label}>
          <span style={S.labelText}>口令</span>
          <input
            style={S.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        {err && <div style={S.err}>{err}</div>}

        <button type="submit" style={{ ...S.btn, opacity: busy || !name.trim() ? 0.5 : 1 }} disabled={busy || !name.trim()}>
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}

const S: Record<string, CSSProperties> = {
  wrap: { minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 20 },
  card: {
    width: "100%",
    maxWidth: 360,
    display: "flex",
    flexDirection: "column",
    gap: 14,
    padding: "28px 26px",
    background: "#ffffff",
    border: "1px solid #e3e6ea",
    borderRadius: 16,
    boxShadow: "0 24px 60px -28px rgba(28,37,48,0.35)",
  },
  brand: { fontSize: 19, fontWeight: 600, color: "#1c2530" },
  dot: { color: "#3b6ea5" },
  sub: { fontSize: 13, color: "#7a838e", marginTop: -8, marginBottom: 4 },
  label: { display: "flex", flexDirection: "column", gap: 5 },
  labelText: { fontSize: 12, color: "#7a838e" },
  input: {
    padding: "9px 11px",
    fontSize: 14,
    border: "1px solid #d4d8dd",
    borderRadius: 9,
    outline: "none",
    background: "#fbfbfc",
    color: "#1c2530",
  },
  err: {
    fontSize: 12.5,
    color: "#b4413c",
    background: "#fbeceb",
    padding: "8px 10px",
    borderRadius: 8,
  },
  btn: {
    marginTop: 4,
    padding: "10px 12px",
    fontSize: 14,
    fontWeight: 600,
    color: "#ffffff",
    background: "#3b6ea5",
    border: "none",
    borderRadius: 9,
    cursor: "pointer",
  },
};
