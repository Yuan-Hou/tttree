import { useEffect, useState, type CSSProperties } from "react";
import { Login } from "./Login";
import { Admin } from "./Admin";
import { brandTitle, syncBrand } from "./brand";
import {
  clearSession,
  loadSession,
  redirectToApp,
  saveSession,
  type Session,
} from "./session";
import type { LoginResp } from "./api";

type View = "login" | "landing" | "admin";

/** 登录项目根:普通用户登录后立刻交接到创作台;管理员落在「落地选择」(创作台 / 管理控制台)。
 *  仅管理员会话落本地(刷新仍在),普通用户不在此停留。 */
export function App() {
  // 仅恢复管理员会话(普通用户不存)。
  const restored = loadSession();
  const [view, setView] = useState<View>(restored?.is_admin ? "landing" : "login");
  const [session, setSession] = useState<Session | null>(restored?.is_admin ? restored : null);
  const [, setBrandTick] = useState(0);

  useEffect(() => {
    syncBrand().then((ok) => ok && setBrandTick((n) => n + 1));
  }, []);
  useEffect(() => {
    document.title = `登录 · ${brandTitle()}`;
  }, [view]);

  const onLoggedIn = (resp: LoginResp) => {
    if (resp.is_admin) {
      saveSession(resp);
      setSession(resp);
      setView("landing");
    } else {
      redirectToApp(resp); // 普通用户:直接进创作台
    }
  };

  const logout = () => {
    clearSession();
    setSession(null);
    setView("login");
  };

  if (view === "admin" && session) {
    return <Admin session={session} onBack={() => setView("landing")} onLogout={logout} />;
  }
  if (view === "landing" && session) {
    return (
      <Landing
        session={session}
        onEnterApp={() => redirectToApp(session)}
        onEnterAdmin={() => setView("admin")}
        onLogout={logout}
      />
    );
  }
  return <Login onLoggedIn={onLoggedIn} />;
}

function Landing({
  session,
  onEnterApp,
  onEnterAdmin,
  onLogout,
}: {
  session: Session;
  onEnterApp: () => void;
  onEnterAdmin: () => void;
  onLogout: () => void;
}) {
  return (
    <div style={S.wrap}>
      <div style={S.card}>
        <div style={S.brand}>
          <span style={S.dot}>◆</span> {brandTitle()}
        </div>
        <div style={S.sub}>
          欢迎,{session.name} · 管理员
        </div>
        <button style={S.primary} onClick={onEnterApp}>
          进入创作台
        </button>
        <button style={S.secondary} onClick={onEnterAdmin}>
          进入管理控制台
        </button>
        <button style={S.link} onClick={onLogout}>
          退出登录
        </button>
      </div>
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
    gap: 12,
    padding: "28px 26px",
    background: "#ffffff",
    border: "1px solid #e3e6ea",
    borderRadius: 16,
    boxShadow: "0 24px 60px -28px rgba(28,37,48,0.35)",
  },
  brand: { fontSize: 19, fontWeight: 600, color: "#1c2530" },
  dot: { color: "#3b6ea5" },
  sub: { fontSize: 13, color: "#7a838e", marginTop: -6, marginBottom: 6 },
  primary: {
    padding: "10px 12px",
    fontSize: 14,
    fontWeight: 600,
    color: "#ffffff",
    background: "#3b6ea5",
    border: "none",
    borderRadius: 9,
    cursor: "pointer",
  },
  secondary: {
    padding: "10px 12px",
    fontSize: 14,
    fontWeight: 600,
    color: "#3b6ea5",
    background: "#eef3f9",
    border: "1px solid #d4e0ee",
    borderRadius: 9,
    cursor: "pointer",
  },
  link: {
    marginTop: 2,
    padding: "6px",
    fontSize: 12.5,
    color: "#7a838e",
    background: "none",
    border: "none",
    cursor: "pointer",
  },
};
