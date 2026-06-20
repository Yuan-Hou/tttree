import { useEffect, useMemo, useState } from "react";
import { getBibles, saveBibles, type BibleSection } from "../api";
import { Button } from "./ui";

type Kind = "style" | "visual";

const COPY: Record<Kind, { title: string; blurb: string; placeholder: string }> = {
  style: {
    title: "文风圣经",
    blurb:
      "叙事文风总则 —— 作为 system 前缀注入导演 A / Writer / 导演 B / 选项 agent,决定全篇的语气、人称、节奏。" +
      "留空则用全局打包默认;填写后本故事改用你的版本(只影响本故事的后续轮)。",
    placeholder: "留空 = 用全局默认文风圣经。\n或从上方「载入模板」挑一份预制版,再按需修改。",
  },
  visual: {
    title: "画风圣经",
    blurb:
      "绘图风格总则 —— 注入绘图写稿 Agent 的易变区,决定画面的媒介、色调、构图语言。" +
      "留空则用全局打包默认;填写后本故事改用你的版本。",
    placeholder: "留空 = 用全局默认画风圣经。\n或从上方「载入模板」挑一份预制版,再按需修改。",
  },
};

/** 文风 / 画风圣经编辑(故事内设置 · bible 子步):载入 → (可选)套模板 → 编辑 → 保存。
 *  模板由后端启动时扫描 prompts/<kind>_bible/*.md 提供,点一份即载入编辑框(需手动保存才落库)。
 *  编辑框留空 = 清空自定义 → 回退全局打包默认(占位文案已说明)。 */
export function BibleEditor({ storyId, kind }: { storyId: string; kind: Kind }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState(""); // 已落库版本,用于判脏
  const [section, setSection] = useState<BibleSection | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    getBibles(storyId)
      .then((d) => {
        if (!alive) return;
        const sec = kind === "style" ? d.style : d.visual;
        setSection(sec);
        setText(sec.custom);
        setSaved(sec.custom);
      })
      .catch((e) => alive && setErr(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [storyId, kind]);

  const dirty = text !== saved;
  const copy = COPY[kind];

  // 当前编辑框内容若与某模板逐字一致,下拉回显该模板名;否则回到占位项。
  const matchedTemplate = useMemo(
    () => section?.templates.find((t) => t.content === text)?.name ?? "",
    [section, text],
  );

  const applyTemplate = (name: string) => {
    const tpl = section?.templates.find((t) => t.name === name);
    if (tpl) setText(tpl.content); // 仅载入编辑框,不落库
  };

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body = kind === "style" ? { style_bible: text } : { visual_style_bible: text };
      const d = await saveBibles(storyId, body);
      const sec = kind === "style" ? d.style : d.visual;
      setSection(sec);
      setSaved(sec.custom);
      setText(sec.custom);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <p className="text-[12.5px] leading-relaxed text-ink-soft">{copy.blurb}</p>

      {/* 模板选择:点一份预制圣经载入编辑框(需手动保存) */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-ink-faint">载入模板</span>
        <select
          value={matchedTemplate}
          disabled={loading || !section || section.templates.length === 0}
          onChange={(e) => e.target.value && applyTemplate(e.target.value)}
          className="rounded-lg border border-line-strong bg-paper px-2.5 py-1.5 text-[12.5px] text-ink focus:border-accent focus:outline-none disabled:opacity-50"
        >
          <option value="">选择预制模板…</option>
          {section?.templates.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name === "default" ? "default(全局默认)" : t.name}
            </option>
          ))}
        </select>
        {text !== "" && (
          <button
            onClick={() => setText("")}
            className="rounded-lg px-2.5 py-1.5 text-[12px] text-ink-soft transition hover:bg-sunken hover:text-ink"
          >
            清空(用默认)
          </button>
        )}
      </div>

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-[13px] text-ink-faint">载入中…</div>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          placeholder={copy.placeholder}
          className="min-h-0 flex-1 resize-none rounded-xl border border-line-strong bg-paper px-4 py-3 font-serif text-[14px] leading-relaxed text-ink focus:border-accent focus:outline-none"
        />
      )}

      {err && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{err}</p>}

      <div className="flex items-center gap-3">
        <Button variant="primary" disabled={!dirty || busy || loading} onClick={save}>
          {busy ? "保存中…" : `保存${copy.title}`}
        </Button>
        <span className="font-mono text-[11px] text-ink-faint">
          {dirty ? "● 有未保存的改动" : text === "" ? "未自定义 · 用全局默认" : "已与存档一致"}
          <span className="ml-2">{text.length} 字</span>
        </span>
      </div>
    </div>
  );
}
