import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

/** 全局轻提示(toast)。失败时调 useToast()(msg) 弹一条自动消失的提示;详情仍在对应节点里看。
 *  与 lightbox 同构:Provider 挂根部,任意组件 useToast() 取触发函数。 */
type Tone = "error" | "info";
type ShowToast = (message: string, tone?: Tone) => void;
interface Toast {
  id: number;
  message: string;
  tone: Tone;
}

const ToastCtx = createContext<ShowToast>(() => {});
export const useToast = (): ShowToast => useContext(ToastCtx);

const AUTO_DISMISS_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const show = useCallback<ShowToast>(
    (message, tone = "error") => {
      const id = ++idRef.current;
      setToasts((t) => [...t, { id, message, tone }]);
      setTimeout(() => dismiss(id), AUTO_DISMISS_MS); // 自动消失
    },
    [dismiss],
  );

  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[70] flex flex-col items-end gap-2">
        {toasts.map((t) => (
          <button
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={`toast-in pointer-events-auto max-w-[380px] rounded-xl border px-4 py-2.5 text-left text-[13px] leading-snug shadow-[0_10px_30px_-10px_rgba(28,37,48,0.45)] ${
              t.tone === "error"
                ? "border-danger/30 bg-danger-soft text-danger"
                : "border-line-strong bg-surface text-ink"
            }`}
            title="点击关闭"
          >
            {t.tone === "error" && <span className="mr-1.5">⚠</span>}
            {t.message}
          </button>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
