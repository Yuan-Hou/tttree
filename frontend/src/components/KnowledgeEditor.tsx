import { useEffect, useState } from "react";
import { getKnowledge, saveKnowledge } from "../api";
import { Button } from "./ui";

/** 知识库编辑(故事内设置 · 子步二):整篇自由文本的载入 → 编辑 → 保存。
 *  知识库是用户精选的「恒定设定底座」(角色人设/世界观/关系),只注入导演 A,agent 只读。 */
export function KnowledgeEditor({ storyId }: { storyId: string }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState(""); // 已落库版本,用于判脏
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(null);
    getKnowledge(storyId)
      .then((d) => {
        if (!alive) return;
        setText(d.content);
        setSaved(d.content);
      })
      .catch((e) => alive && setErr(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [storyId]);

  const dirty = text !== saved;

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const d = await saveKnowledge(storyId, text);
      setSaved(d.content);
      setText(d.content);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <p className="text-[12.5px] leading-relaxed text-ink-soft">
        故事的「设定圣经」——角色人设、世界观、关系等恒定底座。由你撰写、AI 只读,只注入导演 A 的判断上下文,
        不随剧情变动(那是黑板的事)。整篇自由文本,自己组织。
      </p>

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-[13px] text-ink-faint">载入中…</div>
      ) : (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
          placeholder="例如:&#10;主角林屿,二十岁,沉默寡言,左手有旧伤。&#10;世界观:潮汐之城,每日两度被海水淹没半城……"
          className="min-h-0 flex-1 resize-none rounded-xl border border-line-strong bg-paper px-4 py-3 font-serif text-[14px] leading-relaxed text-ink focus:border-accent focus:outline-none"
        />
      )}

      {err && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[12px] text-danger">出错:{err}</p>}

      <div className="flex items-center gap-3">
        <Button variant="primary" disabled={!dirty || busy || loading} onClick={save}>
          {busy ? "保存中…" : "保存知识库"}
        </Button>
        <span className="font-mono text-[11px] text-ink-faint">
          {dirty ? "● 有未保存的改动" : "已与存档一致"}
          <span className="ml-2">{text.length} 字</span>
        </span>
      </div>
    </div>
  );
}
