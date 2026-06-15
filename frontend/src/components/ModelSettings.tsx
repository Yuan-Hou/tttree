import { useEffect, useMemo, useState } from "react";
import { getSettings, saveSettings } from "../api";
import type { ModelChoice } from "../types";
import { Button } from "./ui";

/** 可单独选模型的 agent(与后端 StorySettings 的 *_model 列一一对应)。 */
const AGENTS: { key: string; label: string; hint: string }[] = [
  { key: "director_a", label: "导演 A · 预案", hint: "读黑板 + 知识库,定本轮写作纲要" },
  { key: "writer", label: "写作", hint: "据纲要写叙事正文(纯文本流)" },
  { key: "director_b", label: "导演 B · 复盘", hint: "据成稿重写黑板、提配图建议" },
  { key: "illustrator", label: "绘图写稿", hint: "为出图写提示词稿(JSON)" },
];

const DEEPSEEK = "deepseek-v4-pro";

/** 模型设置(故事内设置 · 子步四 UI):全局默认 + 各 agent 覆盖。与故事绑定,随 fork/delete 连带。 */
export function ModelSettings({ storyId }: { storyId: string }) {
  const [models, setModels] = useState<ModelChoice[]>([]);
  const [defaultModel, setDefaultModel] = useState(DEEPSEEK);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(""); // 已落库快照(判脏)
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    getSettings(storyId)
      .then((s) => {
        if (!alive) return;
        setModels(s.models);
        setDefaultModel(s.default_model);
        setOverrides(s.overrides);
        setSaved(JSON.stringify({ default_model: s.default_model, overrides: s.overrides }));
      })
      .catch((e) => alive && setErr(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [storyId]);

  const labelOf = useMemo(() => {
    const m = new Map(models.map((x) => [x.id, x.label]));
    return (id: string) => m.get(id) ?? id;
  }, [models]);

  const cur = JSON.stringify({ default_model: defaultModel, overrides });
  const dirty = cur !== saved;
  // 任何 agent 实际生效的是非 deepseek → 提示放弃缓存红利
  const usesNonDeepseek = AGENTS.some((a) => (overrides[a.key] || defaultModel) !== DEEPSEEK);

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await saveSettings(storyId, { default_model: defaultModel, overrides });
      setDefaultModel(s.default_model);
      setOverrides(s.overrides);
      setSaved(JSON.stringify({ default_model: s.default_model, overrides: s.overrides }));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="flex flex-1 items-center justify-center text-[13px] text-ink-faint">载入中…</div>;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <p className="text-[12.5px] leading-relaxed text-ink-soft">
        每个 agent 用哪个 LLM。可单独指定,或「用全局默认」。只接 OpenAI 兼容模型。新故事默认全部 DeepSeek
        (行为与从前一致)。设置随副本复制、随删除清理。
      </p>

      {/* 全局默认 */}
      <div className="rounded-xl border border-line bg-paper p-3.5">
        <div className="flex items-center gap-3">
          <div className="min-w-0">
            <div className="text-[13.5px] font-medium text-ink">全局默认模型</div>
            <div className="mt-0.5 text-[11.5px] text-ink-faint">未单独指定的 agent 都用它</div>
          </div>
          <select
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
            className="ml-auto rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* 各 agent 覆盖 */}
      <div className="flex flex-col gap-2">
        {AGENTS.map((a) => {
          const ov = overrides[a.key] || "";
          const effective = ov || defaultModel;
          return (
            <div key={a.key} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3">
              <div className="min-w-0 flex-1">
                <div className="text-[13.5px] font-medium text-ink">{a.label}</div>
                <div className="mt-0.5 text-[11.5px] text-ink-faint">{a.hint}</div>
              </div>
              <div className="flex flex-col items-end gap-1">
                <select
                  value={ov}
                  onChange={(e) => setOverrides((o) => ({ ...o, [a.key]: e.target.value }))}
                  className="rounded-lg border border-line-strong bg-paper px-2.5 py-1.5 text-[12.5px] text-ink focus:border-accent focus:outline-none"
                >
                  <option value="">用全局默认</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
                <span className="font-mono text-[10px] text-ink-faint">
                  生效:{labelOf(effective)}
                  {!ov && " · 默认"}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {usesNonDeepseek && (
        <p className="rounded-lg border border-line bg-paper px-3 py-2 text-[11.5px] leading-snug text-ink-faint">
          注:切到非 DeepSeek 的 agent 会放弃 DeepSeek 的前缀缓存红利(该 agent 全量历史全价重发)——
          这是多模型自由的代价。
        </p>
      )}

      {err && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{err}</p>}

      <div className="flex items-center gap-3">
        <Button variant="primary" disabled={!dirty || busy} onClick={save}>
          {busy ? "保存中…" : "保存模型设置"}
        </Button>
        <span className="font-mono text-[11px] text-ink-faint">{dirty ? "● 有未保存的改动" : "已与存档一致"}</span>
      </div>
    </div>
  );
}
