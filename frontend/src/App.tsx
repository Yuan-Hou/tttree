import { useStoryEngine } from "./useStoryEngine";
import { Bookshelf } from "./components/Bookshelf";
import { ReadingColumn } from "./components/ReadingColumn";
import { Composer } from "./components/Composer";
import { StatePanel } from "./components/StatePanel";
import { ScenesPanel } from "./components/ScenesPanel";
import { DrawDeck } from "./components/DrawDeck";
import { ManualDeck } from "./components/ManualDeck";
import { Workbench } from "./components/Workbench";
import { SettingsPanel } from "./components/SettingsPanel";
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
              <div className="ml-auto flex items-center gap-4">
                <span className="font-mono text-[11px] text-ink-faint">
                  {chapters ? `${chapters} 拍已写就` : "尚未落笔"}
                </span>
                <button
                  onClick={e.openScope}
                  className="relative flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
                  title="摊开本轮三段式内部流:看进度、看每个 agent 的上下文、回退/重试/副本"
                >
                  <span className="text-accent">⊞</span> 导演工作台
                  {e.turnStreaming && (
                    <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-accent ring-2 ring-paper breathe" />
                  )}
                </button>
                <button
                  onClick={e.openSettings}
                  className="flex items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
                  title="故事内设置:知识库、参考图库、各 agent 模型(随副本复制、随删除清理)"
                >
                  <span className="text-accent">⚙</span> 设置
                </button>
              </div>
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
              scenesDrafts={e.scenesDrafts}
              pending={e.pending}
              onDraw={(slug) => e.openDraft(slug, "user_initiated")}
            />
            <ManualDeck
              drafts={e.drafts}
              onEditPrompt={e.editDraftPrompt}
              onSetRefs={e.setDraftRefs}
              onConfirm={e.confirmDraft}
              onReuse={(k) => e.decideDraft(k, "reuse")}
              onSkip={(k) => e.decideDraft(k, "skip")}
              onDismiss={e.dropDraft}
            />
            <DrawDeck storyId={e.curId} drawsVersion={e.drawsVersion} onReload={e.reloadScope} />
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

      {/* 导演工作台:平时收起不占阅读空间,展开为覆盖层大视图 */}
      {e.scopeOpen && e.curId && (
        <Workbench
          onClose={e.closeScope}
          storyId={e.curId}
          title={e.title}
          turns={e.turns}
          scopeTurn={e.scopeTurn}
          setScopeTurn={e.setScopeTurn}
          latestTurn={e.latestTurn}
          liveStages={e.liveStages}
          liveTurn={e.liveTurn}
          turnStreaming={e.turnStreaming}
          retrying={e.retrying}
          contextsVersion={e.contextsVersion}
          drawsVersion={e.drawsVersion}
          proposals={e.proposals}
          onRetry={e.doRetry}
          onRollback={e.doRollback}
          onFork={e.doFork}
          saveStepContext={e.saveStepContext}
          reloadScope={e.reloadScope}
        />
      )}

      {/* 故事内设置:知识库 / 图库(模型设置子步四并入)。覆盖层。 */}
      {e.settingsOpen && e.curId && (
        <SettingsPanel storyId={e.curId} title={e.title} onClose={e.closeSettings} />
      )}
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
