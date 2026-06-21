import { useState } from "react";
import { changePassword, updateMe } from "../api";
import { setName as cacheName } from "../auth";

/** 个人信息与安全:改昵称(= 登录名,同一字段)+ 改密码。改昵称成功后更新本地缓存并上抛父级刷新书架。 */
export function PersonalInfo({
  username,
  onNameChange,
}: {
  username: string;
  onNameChange?: (name: string) => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      <NicknameCard username={username} onNameChange={onNameChange} />
      <PasswordCard />
    </div>
  );
}

function NicknameCard({
  username,
  onNameChange,
}: {
  username: string;
  onNameChange?: (name: string) => void;
}) {
  const [name, setNameInput] = useState(username);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const dirty = name.trim() !== "" && name.trim() !== username;

  const save = async () => {
    if (!dirty || busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await updateMe(name.trim());
      cacheName(r.name);
      onNameChange?.(r.name);
      setNameInput(r.name);
      setMsg({ ok: true, text: "已保存" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-2 text-[13px] text-ink-soft">昵称(也是登录名)</div>
      <div className="flex gap-2">
        <input
          className="min-w-0 flex-1 rounded-lg border border-line bg-paper px-3 py-2 text-[13.5px] text-ink outline-none focus:border-accent"
          value={name}
          maxLength={40}
          onChange={(e) => setNameInput(e.target.value)}
        />
        <button
          onClick={save}
          disabled={!dirty || busy}
          className="shrink-0 rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white transition disabled:opacity-40"
        >
          保存
        </button>
      </div>
      {msg && (
        <div className={`mt-1.5 text-[11.5px] ${msg.ok ? "text-ink-faint" : "text-danger"}`}>{msg.text}</div>
      )}
    </div>
  );
}

function PasswordCard() {
  const [oldPw, setOld] = useState("");
  const [newPw, setNew] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const mismatch = confirm !== "" && newPw !== confirm;
  const ready = oldPw !== "" && newPw !== "" && newPw === confirm && !busy;

  const save = async () => {
    if (!ready) return;
    setBusy(true);
    setMsg(null);
    try {
      await changePassword(oldPw, newPw);
      setOld("");
      setNew("");
      setConfirm("");
      setMsg({ ok: true, text: "口令已更新" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const field = (label: string, value: string, set: (v: string) => void) => (
    <label className="flex flex-col gap-1">
      <span className="text-[11.5px] text-ink-faint">{label}</span>
      <input
        type="password"
        className="rounded-lg border border-line bg-paper px-3 py-2 text-[13.5px] text-ink outline-none focus:border-accent"
        value={value}
        onChange={(e) => set(e.target.value)}
        autoComplete="off"
      />
    </label>
  );

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-2.5 text-[13px] text-ink-soft">修改口令</div>
      <div className="flex flex-col gap-2.5">
        {field("原口令", oldPw, setOld)}
        {field("新口令", newPw, setNew)}
        {field("确认新口令", confirm, setConfirm)}
        {mismatch && <div className="text-[11.5px] text-danger">两次输入不一致</div>}
        <button
          onClick={save}
          disabled={!ready}
          className="mt-1 self-start rounded-lg bg-accent px-4 py-2 text-[13px] font-medium text-white transition disabled:opacity-40"
        >
          更新口令
        </button>
        {msg && (
          <div className={`text-[11.5px] ${msg.ok ? "text-ink-faint" : "text-danger"}`}>{msg.text}</div>
        )}
      </div>
    </div>
  );
}
