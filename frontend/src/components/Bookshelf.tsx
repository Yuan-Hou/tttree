import { useState } from "react";
import type { StoryInfo } from "../types";
import { Button, Eyebrow } from "./ui";

interface Props {
  stories: StoryInfo[];
  curId: string | null;
  onSelect: (id: string) => void;
  onCreate: (title: string) => void;
  onDelete: (id: string) => void;
}

export function Bookshelf({ stories, curId, onSelect, onCreate, onDelete }: Props) {
  const [title, setTitle] = useState("");
  const submit = () => {
    const t = title.trim();
    if (!t) return;
    setTitle("");
    onCreate(t);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-5 pb-3 pt-6">
        <Eyebrow>书架</Eyebrow>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3">
        {stories.length === 0 && (
          <p className="px-2 py-4 text-[12.5px] leading-relaxed text-ink-faint">
            还没有故事。在下方起一个名字,种下第一棵。
          </p>
        )}
        <ul className="flex flex-col gap-0.5">
          {stories.map((s) => {
            const active = s.id === curId;
            return (
              <li key={s.id}>
                <div
                  className={`group flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors ${
                    active ? "bg-accent-soft" : "hover:bg-sunken"
                  }`}
                >
                  {/* 生长节点:激活=实心松针,其余=空心环 */}
                  <span
                    className={`h-2 w-2 shrink-0 rounded-full ${
                      active ? "bg-accent" : "border border-line-strong"
                    }`}
                  />
                  <button
                    onClick={() => onSelect(s.id)}
                    className="min-w-0 flex-1 text-left"
                    title={s.id}
                  >
                    <div
                      className={`truncate text-[13.5px] ${
                        active ? "font-medium text-accent-ink" : "text-ink"
                      }`}
                    >
                      {s.title}
                    </div>
                  </button>
                  <span className="font-mono text-[10.5px] text-ink-faint">{s.turn_count}</span>
                  <button
                    onClick={() => {
                      if (confirm(`删除「${s.title}」?此操作不可撤销。`)) onDelete(s.id);
                    }}
                    className="rounded px-1 text-ink-faint opacity-0 transition group-hover:opacity-100 hover:text-danger"
                    title="删除"
                  >
                    ✕
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="border-t border-line p-3">
        <div className="flex gap-2">
          <input
            value={title}
            onChange={(ev) => setTitle(ev.target.value)}
            onKeyDown={(ev) => ev.key === "Enter" && submit()}
            placeholder="新故事标题"
            className="min-w-0 flex-1 rounded-lg border border-line-strong bg-paper px-3 py-1.5 text-[13px] text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
          />
          <Button variant="primary" onClick={submit} aria-label="新建故事">
            种下
          </Button>
        </div>
      </div>
    </div>
  );
}
