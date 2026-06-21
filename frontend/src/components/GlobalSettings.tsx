import { useEffect, useState } from "react";
import { getGlobalSettings, saveGlobalSettings } from "../api";
import type { EndpointChange, GlobalEndpoint } from "../types";
import { Button } from "./ui";

/** 每个接入点的本地草稿:mode + 当前 URL + 新输入的 key(从不回填,留空=不改 key)。 */
interface Draft {
  mode: "site" | "custom";
  baseUrl: string;
  apiKey: string;
}

function toDraft(e: GlobalEndpoint): Draft {
  return { mode: e.mode, baseUrl: e.base_url, apiKey: "" };
}

/** 全局设置 · 接入点供应商配置(全站单例,不随故事)。每接入点可选「本站点服务」或「自定义 URL + 自填 key」。
 *  自填 key 经后端 APP_SECRET 加密落库;本组件从不收到明文,只见掩码。 */
export function GlobalSettings() {
  const [endpoints, setEndpoints] = useState<GlobalEndpoint[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [cryptoOk, setCryptoOk] = useState(true);
  const [newApiReady, setNewApiReady] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedTick, setSavedTick] = useState(false);

  const load = (alive = { v: true }) => {
    setLoading(true);
    setErr(null);
    getGlobalSettings()
      .then((g) => {
        if (!alive.v) return;
        setEndpoints(g.endpoints);
        setCryptoOk(g.crypto_available);
        setNewApiReady(g.new_api_ready);
        setDrafts(Object.fromEntries(g.endpoints.map((e) => [e.id, toDraft(e)])));
      })
      .catch((e) => alive.v && setErr(String(e)))
      .finally(() => alive.v && setLoading(false));
  };

  useEffect(() => {
    const alive = { v: true };
    load(alive);
    return () => {
      alive.v = false;
    };
  }, []);

  const patch = (id: string, p: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...p } }));

  // 脏:任一接入点 mode/URL 变了,或填了新 key。
  const dirty = endpoints.some((e) => {
    const d = drafts[e.id];
    if (!d) return false;
    return d.mode !== e.mode || (d.mode === "custom" && d.baseUrl !== e.base_url) || d.apiKey !== "";
  });

  const save = async () => {
    // 客户端先挡一道:切到自定义但既无已存 key 又没新填 → 后端会 422,这里先给明确提示。
    for (const e of endpoints) {
      const d = drafts[e.id];
      if (d.mode === "custom" && !e.key_set && !d.apiKey.trim()) {
        setErr(`「${e.label}」自定义模式需要填写 API key`);
        return;
      }
      if (d.mode === "custom" && !d.baseUrl.trim()) {
        setErr(`「${e.label}」自定义模式需要填写 endpoint`);
        return;
      }
    }
    setBusy(true);
    setErr(null);
    const body: Record<string, EndpointChange> = {};
    for (const e of endpoints) {
      const d = drafts[e.id];
      if (d.mode === "site") body[e.id] = { mode: "site" };
      else
        body[e.id] = {
          mode: "custom",
          base_url: d.baseUrl.trim(),
          ...(d.apiKey.trim() ? { api_key: d.apiKey.trim() } : {}),
        };
    }
    try {
      const g = await saveGlobalSettings(body);
      setEndpoints(g.endpoints);
      setCryptoOk(g.crypto_available);
      setNewApiReady(g.new_api_ready);
      setDrafts(Object.fromEntries(g.endpoints.map((e) => [e.id, toDraft(e)])));
      setSavedTick(true);
      setTimeout(() => setSavedTick(false), 1600);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading)
    return <div className="flex flex-1 items-center justify-center text-[13px] text-ink-faint">载入中…</div>;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <p className="text-[12.5px] leading-relaxed text-ink-soft">
        各供应商接入点的 endpoint 与 API key。默认「本站点服务」——经 new-api 网关、用你的专属 token,
        登录时自动补齐,你无需填写。切到「自定义」可指向自己的网关并填自己的 key（加密保存，本页只显示掩码）。
        此设置按你的账号隔离,不随故事。
      </p>

      {!newApiReady && (
        <p className="rounded-lg border border-line bg-paper px-3 py-2 text-[11.5px] leading-snug text-danger">
          你的 new-api 模型 key 尚未就绪——「本站点服务」此刻不可用(重新登录会自动补齐;
          或切到「自定义」填你自己的 key)。
        </p>
      )}

      {!cryptoOk && (
        <p className="rounded-lg border border-line bg-paper px-3 py-2 text-[11.5px] leading-snug text-danger">
          未配置 APP_SECRET：无法保存「自定义」的 API key,只能使用「本站点服务」。
          在后端 .env 设置 APP_SECRET 后即可启用自填 key。
        </p>
      )}

      <div className="flex flex-col gap-2.5">
        {endpoints.map((e) => {
          const d = drafts[e.id];
          if (!d) return null;
          const custom = d.mode === "custom";
          return (
            <div key={e.id} className="rounded-xl border border-line bg-surface p-3.5">
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-medium text-ink">
                    {e.label}
                    <span className="ml-2 rounded-[5px] bg-paper px-1.5 py-px font-mono text-[9.5px] text-ink-faint">
                      {e.group}
                    </span>
                  </div>
                  {!custom && (
                    <div className="mt-0.5 truncate font-mono text-[10.5px] text-ink-faint">
                      本站点服务 · 经 new-api · {e.site_effective_base}
                    </div>
                  )}
                </div>
                {/* 模式切换 */}
                <div className="flex shrink-0 overflow-hidden rounded-lg border border-line-strong">
                  {(["site", "custom"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      disabled={m === "custom" && !cryptoOk}
                      onClick={() => patch(e.id, { mode: m })}
                      className={`px-2.5 py-1 text-[12px] transition ${
                        d.mode === m
                          ? "bg-accent-soft text-accent-ink"
                          : "bg-paper text-ink-soft hover:bg-sunken disabled:opacity-40"
                      }`}
                    >
                      {m === "site" ? "本站点服务" : "自定义"}
                    </button>
                  ))}
                </div>
              </div>

              {custom && (
                <div className="mt-3 flex flex-col gap-2 border-t border-line pt-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-faint">Endpoint</span>
                    <input
                      value={d.baseUrl}
                      onChange={(ev) => patch(e.id, { baseUrl: ev.target.value })}
                      placeholder={e.site_base_url}
                      className="rounded-lg border border-line-strong bg-paper px-2.5 py-1.5 font-mono text-[12px] text-ink focus:border-accent focus:outline-none"
                    />
                    {e.presets.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {e.presets.map((u) => (
                          <button
                            key={u}
                            type="button"
                            onClick={() => patch(e.id, { baseUrl: u })}
                            className="rounded-md border border-line bg-paper px-2 py-0.5 font-mono text-[10px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
                          >
                            {u}
                          </button>
                        ))}
                      </div>
                    )}
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[11px] text-ink-faint">
                      API key
                      {e.key_set && (
                        <span className="ml-2 font-mono text-ink-faint">已存 {e.key_masked} · 留空=不改</span>
                      )}
                    </span>
                    <input
                      type="password"
                      value={d.apiKey}
                      onChange={(ev) => patch(e.id, { apiKey: ev.target.value })}
                      placeholder={e.key_set ? "（不改则留空）" : "粘贴你的 API key"}
                      autoComplete="off"
                      className="rounded-lg border border-line-strong bg-paper px-2.5 py-1.5 font-mono text-[12px] text-ink focus:border-accent focus:outline-none"
                    />
                  </label>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {err && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{err}</p>}

      <div className="flex items-center gap-3">
        <Button variant="primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "保存中…" : "保存全局设置"}
        </Button>
        <span className="font-mono text-[11px] text-ink-faint">
          {savedTick ? "✓ 已保存" : dirty ? "● 有未保存的改动" : "已与存档一致"}
        </span>
      </div>
    </div>
  );
}
