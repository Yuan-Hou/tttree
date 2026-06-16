import { useCallback, useMemo, useState } from "react";
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
import { SceneMap } from "./components/SceneMap";
import { Eyebrow } from "./components/ui";

export function App() {
  const e = useStoryEngine();
  const chapters = e.turns.length;

  // 点地图实线 → 滚动对话列到对应轮的开头。对话与地图左右并列、始终挂载,直接滚即可。
  const jumpToTurn = useCallback((turnIndex: number) => {
    document.getElementById(`turn-${turnIndex}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // 单击对话区 → 让地图聚焦该轮落点场景节点(放大 + 翻到对应图)。nonce 保证连点同一轮也重触发。
  const [focusReq, setFocusReq] = useState<{ turnIndex: number; nonce: number } | null>(null);
  const focusTurnOnMap = useCallback((turnIndex: number) => {
    setFocusReq({ turnIndex, nonce: Date.now() });
  }, []);

  // 地图随故事推进/新出图自动刷新:轮数、正典图总数、绘图版本、当前场景任一变 → 重取地图
  const canonImageCount = useMemo(
    () => Object.values(e.scenesImages).reduce((a, v) => a + v.length, 0),
    [e.scenesImages],
  );
  const mapRefreshKey = `${e.turns.length}|${canonImageCount}|${e.drawsVersion}|${e.blackboard?.story_meta?.current_scene ?? ""}`;

  return (
    <div className="grid h-full grid-cols-[248px_minmax(0,1fr)_minmax(0,1fr)_344px] bg-paper text-ink">
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
            <ReadingColumn turns={e.turns} onTurnClick={focusTurnOnMap} />
            <Composer disabled={!e.curId} streaming={e.turnStreaming} onSubmit={e.submitTurn} />
          </>
        ) : (
          <EmptyReading />
        )}
      </main>

      {/* ── 地图:与对话左右并列的常驻列 ── */}
      <section className="flex min-h-0 flex-col border-l border-line bg-paper">
        {e.curId ? (
          <SceneMap storyId={e.curId} onJumpToTurn={jumpToTurn} refreshKey={mapRefreshKey} focusReq={focusReq} />
        ) : (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] text-ink-faint">
            选一卷故事,这里会画出场景地图。
          </div>
        )}
      </section>

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
