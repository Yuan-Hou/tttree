import type { Blackboard } from "../types";
import { Eyebrow, Tag } from "./ui";

/** 此刻:当前黑板的安静读出。labeled 行,不抢叙事主区。 */
export function StatePanel({ blackboard }: { blackboard: Blackboard }) {
  const meta = blackboard.story_meta ?? {};
  const chars = Object.entries(blackboard.characters ?? {});
  const items = Object.entries(blackboard.items ?? {});
  const notes = blackboard.notes ?? [];

  return (
    <section className="border-b border-line px-6 py-5">
      <Eyebrow>此刻</Eyebrow>

      <div className="mt-3.5 flex flex-col gap-3.5">
        <Row label="当前场景">
          {meta.current_scene ? (
            <span className="text-[13.5px] text-ink">
              {blackboard.scenes?.[meta.current_scene]?.name ?? meta.current_scene}
            </span>
          ) : (
            <Dash />
          )}
        </Row>

        <Row label="人物">
          {chars.length ? (
            <div className="flex flex-col gap-1.5">
              {chars.map(([name, c]) => (
                <div key={name} className="text-[13px] leading-snug">
                  <span className="text-ink">{name}</span>
                  {c.location && <span className="text-ink-faint"> · {c.location}</span>}
                  {c.status && <div className="text-[12px] text-ink-soft">{c.status}</div>}
                </div>
              ))}
            </div>
          ) : (
            <Dash />
          )}
        </Row>

        <Row label="物品">
          {items.length ? (
            <div className="flex flex-wrap gap-1.5">
              {items.map(([name, it]) => (
                <span key={name} title={it.owner ?? ""}>
                  <Tag>{name}</Tag>
                </span>
              ))}
            </div>
          ) : (
            <Dash />
          )}
        </Row>

        <Row label="伏笔">
          {notes.length ? (
            <ul className="flex flex-col gap-1.5">
              {notes.map((n, i) => (
                <li key={i} className="text-[12.5px] leading-snug text-ink-soft">
                  · {n.content}
                </li>
              ))}
            </ul>
          ) : (
            <Dash />
          )}
        </Row>
      </div>
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)] items-baseline gap-3">
      <div className="font-mono text-[11px] text-ink-faint">{label}</div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

const Dash = () => <span className="text-ink-faint">—</span>;
