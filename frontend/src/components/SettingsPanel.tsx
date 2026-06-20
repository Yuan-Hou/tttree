import { useEffect, useState } from "react";
import type { SettingsSection } from "../types";
import { BibleEditor } from "./BibleEditor";
import { GalleryEditor } from "./GalleryEditor";
import { GlobalSettings } from "./GlobalSettings";
import { KnowledgeEditor } from "./KnowledgeEditor";
import { ModelSettings } from "./ModelSettings";

interface Props {
  storyId: string;
  title: string;
  onClose: () => void;
  initialSection?: SettingsSection; // 打开时直达的分区(工作台「数据源」节点用);省略=模型
}

// 故事内设置的分区:模型 / 知识库 / 文风 / 画风 / 图库。都与故事绑定,随 fork 复制、随 delete 清理。
const SECTIONS = [
  { id: "model", label: "模型", hint: "各 agent 用哪个 LLM" },
  { id: "knowledge", label: "知识库", hint: "设定圣经 · 只注入导演 A" },
  { id: "style", label: "文风圣经", hint: "叙事文风 · 可套模板" },
  { id: "visual", label: "画风圣经", hint: "绘图风格 · 可套模板" },
  { id: "gallery", label: "图库", hint: "参考图素材" },
  { id: "global", label: "全局设置", hint: "供应商接入点 · 全站共享" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

/** 故事内设置:与故事绑定(随 fork 复制、随 delete 清理)的配置面板。覆盖层大视图,延续冷白基调。 */
export function SettingsPanel({ storyId, title, onClose, initialSection }: Props) {
  const [section, setSection] = useState<SectionId>(initialSection ?? "model");

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink/20 p-5 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="mx-auto flex min-h-0 w-full max-w-[920px] flex-1 flex-col overflow-hidden rounded-2xl border border-line-strong bg-paper shadow-[0_24px_60px_-20px_rgba(28,37,48,0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center gap-3 border-b border-line bg-surface px-6 py-3.5">
          <span className="font-serif text-[16px] text-accent-ink">
            <span className="text-accent">⚙</span> 故事内设置
          </span>
          <span className="font-serif text-[14px] text-ink-soft">{title}</span>
          <span className="ml-2 font-mono text-[10.5px] text-ink-faint">随副本复制 · 随删除清理</span>
          <button
            onClick={onClose}
            className="ml-auto rounded-lg px-2.5 py-1 text-[12.5px] text-ink-soft transition hover:bg-sunken hover:text-ink"
          >
            关闭 ✕
          </button>
        </header>

        <div className="flex min-h-0 flex-1">
          {/* 分区导航 */}
          <nav className="flex w-[184px] shrink-0 flex-col gap-1 border-r border-line bg-surface p-3">
            {SECTIONS.map((s) => {
              const active = s.id === section;
              return (
                <button
                  key={s.id}
                  onClick={() => setSection(s.id)}
                  className={`rounded-lg px-3 py-2 text-left transition ${
                    active ? "bg-accent-soft text-accent-ink" : "text-ink-soft hover:bg-sunken"
                  }`}
                >
                  <div className="text-[13.5px] font-medium">{s.label}</div>
                  <div className="mt-0.5 text-[11px] text-ink-faint">{s.hint}</div>
                </button>
              );
            })}
          </nav>

          {/* 分区内容 */}
          <div className="flex min-h-0 flex-1 flex-col p-5">
            {section === "model" ? (
              <ModelSettings storyId={storyId} />
            ) : section === "knowledge" ? (
              <KnowledgeEditor storyId={storyId} />
            ) : section === "style" ? (
              <BibleEditor storyId={storyId} kind="style" />
            ) : section === "visual" ? (
              <BibleEditor storyId={storyId} kind="visual" />
            ) : section === "global" ? (
              <GlobalSettings />
            ) : (
              <GalleryEditor storyId={storyId} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
