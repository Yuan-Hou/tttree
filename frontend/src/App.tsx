import { useStoryEngine } from "./useStoryEngine";
import { Bookshelf } from "./components/Bookshelf";
import { ReadingColumn } from "./components/ReadingColumn";
import { Composer } from "./components/Composer";
import { StatePanel } from "./components/StatePanel";
import { ScenesPanel } from "./components/ScenesPanel";
import { DraftReview } from "./components/DraftReview";
import { Eyebrow } from "./components/ui";

export function App() {
  const e = useStoryEngine();
  const chapters = e.turns.length;

  return (
    <div className="grid h-full grid-cols-[248px_minmax(0,1fr)_344px] bg-paper text-ink">
      {/* ── 左:书架 ── */}
      <aside className="flex min-h-0 flex-col border-r border-line bg-surface">
        <Bookshelf
          stories={e.stories}
          curId={e.curId}
          onSelect={e.selectStory}
          onCreate={e.createStory}
          onDelete={e.removeStory}
        />
      </aside>

      {/* ── 中:阅读列(主角)── */}
      <main className="flex min-h-0 flex-col">
        <header className="flex items-baseline gap-3 border-b border-line px-10 py-4">
          <span className="font-serif text-[15px] font-medium tracking-tight text-accent-ink">
            <span className="text-accent">❡</span> vore-tree
          </span>
          {e.curId ? (
            <>
              <span className="font-serif text-[19px] text-ink">{e.title}</span>
              <span className="ml-auto font-mono text-[11px] text-ink-faint">
                {chapters ? `${chapters} 拍已写就` : "尚未落笔"}
              </span>
            </>
          ) : (
            <span className="ml-auto font-mono text-[11px] text-ink-faint">未选择故事</span>
          )}
        </header>

        {e.curId ? (
          <>
            <ReadingColumn turns={e.turns} />
            <Composer disabled={!e.curId} streaming={e.turnStreaming} onSubmit={e.submitTurn} />
          </>
        ) : (
          <EmptyReading />
        )}
      </main>

      {/* ── 右:此刻 / 场景与画 / 绘图台 ── */}
      <aside className="flex min-h-0 flex-col gap-0 overflow-y-auto border-l border-line bg-surface">
        {e.curId ? (
          <>
            <StatePanel blackboard={e.blackboard} />
            <ScenesPanel
              blackboard={e.blackboard}
              scenesImages={e.scenesImages}
              pending={e.pending}
              onDraw={(slug) => e.openDraft(slug, "user_initiated")}
            />
            <DraftReview
              proposals={e.proposals}
              drafts={e.drafts}
              onProposal={e.startDraftFromProposal}
              onEditPrompt={e.editDraftPrompt}
              onConfirm={e.confirmDraft}
              onReuse={(k) => e.decideDraft(k, "reuse")}
              onSkip={(k) => e.decideDraft(k, "skip")}
              onDismiss={e.dropDraft}
            />
          </>
        ) : (
          <div className="p-6">
            <Eyebrow>此刻</Eyebrow>
            <p className="mt-3 text-[13px] leading-relaxed text-ink-faint">
              选一卷故事,这里会显示当前场景、人物、物品与悬而未决的伏笔。
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}

function EmptyReading() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-10 text-center">
      <div className="font-serif text-[22px] text-ink-soft">一棵尚未生长的故事树</div>
      <p className="mt-3 max-w-sm text-[14px] leading-relaxed text-ink-faint">
        在左侧书架新建或选择一卷故事,然后在下方写下第一个行动。叙事会逐字涌现,插画在后台异步浮现。
      </p>
    </div>
  );
}
