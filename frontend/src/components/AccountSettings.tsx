import { useEffect, useState } from "react";
import { getBalance } from "../api";
import type { Balance } from "../types";
import { GlobalSettings } from "./GlobalSettings";

/** 账户设置(账户级,与故事无关):new-api 余额 + 模型供应接入点配置(原「全局设置」迁来此处)。 */
export function AccountSettings({ username, onClose }: { username: string; onClose: () => void }) {
  const [bal, setBal] = useState<Balance | null>(null);
  const [balErr, setBalErr] = useState<string | null>(null);

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    getBalance()
      .then((b) => alive && setBal(b))
      .catch((e) => alive && setBalErr(String(e)));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink/20 p-5 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="mx-auto flex min-h-0 w-full max-w-[760px] flex-1 flex-col overflow-hidden rounded-2xl border border-line-strong bg-paper shadow-[0_24px_60px_-20px_rgba(28,37,48,0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line bg-surface px-6 py-3.5">
          <span className="font-serif text-[16px] text-accent-ink">
            <span className="text-accent">◑</span> 账户设置
          </span>
          <span className="font-serif text-[14px] text-ink-soft">{username}</span>
          <button
            onClick={onClose}
            className="ml-auto rounded-lg px-2.5 py-1 text-[12.5px] text-ink-soft transition hover:bg-sunken hover:text-ink"
          >
            关闭 ✕
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto p-5">
          <BalanceCard bal={bal} err={balErr} />
          <div className="flex min-h-0 flex-col">
            <div className="mb-2 font-serif text-[14px] text-accent-ink">模型供应</div>
            <GlobalSettings />
          </div>
        </div>
      </div>
    </div>
  );
}

function BalanceCard({ bal, err }: { bal: Balance | null; err: string | null }) {
  const loading = bal == null && err == null;
  const big =
    err || (bal && bal.error)
      ? "—"
      : loading
        ? "…"
        : bal && !bal.ready
          ? "未就绪"
          : bal
            ? `$${bal.balance_usd.toFixed(2)}`
            : "—";
  const note = err
    ? `余额获取失败:${err}`
    : bal?.error
      ? `余额获取失败:${bal.error}`
      : bal && !bal.ready
        ? bal.error || "new-api 账号尚未补齐(重新登录可自动补齐)"
        : "";

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-baseline gap-3">
        <div className="text-[13px] text-ink-soft">new-api 账户余额</div>
        <div className="ml-auto font-mono text-[22px] font-medium text-accent-ink">{big}</div>
      </div>
      {bal && bal.ready && !bal.error && (
        <div className="mt-1 text-right font-mono text-[10.5px] text-ink-faint">
          剩余额度 {bal.quota} · 已用 {bal.used_quota}
        </div>
      )}
      {note && <div className="mt-1.5 text-[11.5px] leading-snug text-danger">{note}</div>}
    </div>
  );
}
