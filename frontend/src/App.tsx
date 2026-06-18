import { useCallback, useMemo, useState } from "react";
import { useStoryEngine } from "./useStoryEngine";
import { useLayout, type DragWhich } from "./useLayout";
import { Bookshelf } from "./components/Bookshelf";
import { ReadingColumn } from "./components/ReadingColumn";
import { Composer } from "./components/Composer";
import { OptionChips } from "./components/OptionChips";
import { StatePanel } from "./components/StatePanel";
import { ScenesPanel } from "./components/ScenesPanel";
import { DrawDeck } from "./components/DrawDeck";
import { ManualDeck } from "./components/ManualDeck";
import { Workbench } from "./components/Workbench";
import { SettingsPanel } from "./components/SettingsPanel";
import { SceneMap } from "./components/SceneMap";
import { Eyebrow } from "./components/ui";

type RightTab = "now" | "scenes" | "draw";

export function App() {
  const e = useStoryEngine();
  const layout = useLayout();
  const chapters = e.turns.length;
  const [tab, setTab] = useState<RightTab>("now");

  // 点地图实线 → 滚动对话列到对应轮的开头。对话与地图左右并列、始终挂载,直接滚即可。
  const jumpToTurn = useCallback((turnIndex: number) => {
    document.getElementById(`turn-${turnIndex}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // 单击对话区 → 让地图聚焦该轮落点场景节点(放大 + 翻到对应图)。nonce 保证连点同一轮也重触发。
  const [focusReq, setFocusReq] = useState<{ turnIndex: number; nonce: number } | null>(null);
  const focusTurnOnMap = useCallback((turnIndex: number) => {
    setFocusReq({ turnIndex, nonce: Date.now() });
  }, []);

  // 选项预填:点输入框上方某条 Options 建议 → 填进 Composer(nonce 保证连点同一条也重填)。
  const [prefill, setPrefill] = useState<{ text: string; key: number } | null>(null);
  const pickOption = useCallback((text: string) => setPrefill({ text, key: Date.now() }), []);
  const [inputFocused, setInputFocused] = useState(false); // 选项条仅在输入框激活时显示

  // 地图随故事推进/新出图自动刷新:轮数、正典图总数、绘图版本、当前场景任一变 → 重取地图
  const canonImageCount = useMemo(
    () => Object.values(e.scenesImages).reduce((a, v) => a + v.length, 0),
    [e.scenesImages],
  );
  const mapRefreshKey = `${e.turns.length}|${canonImageCount}|${e.drawsVersion}|${e.blackboard?.story_meta?.current_scene ?? ""}`;

  const tabs: { key: RightTab; label: string; badge?: number }[] = [
    { key: "now", label: "此刻" },
    { key: "scenes", label: "场景与图", badge: e.drafts.length },
    { key: "draw", label: "绘图台", badge: e.proposals.length },
  ];

  return (
    <div ref={layout.containerRef} className="flex h-full w-full bg-paper text-ink">
      {/* ── 左:书架(可折叠,可拖宽)── */}
      {layout.collapsed ? (
        <aside
          className="flex shrink-0 flex-col items-center gap-4 border-r border-line bg-surface py-5"
          style={{ width: layout.shelfW }}
        >
          <button
            onClick={layout.toggleCollapsed}
            title="展开书架"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-line-strong text-ink-soft transition hover:border-accent hover:text-accent-ink"
          >
            »
          </button>
          <span className="font-mono text-[11px] tracking-widest text-ink-faint [writing-mode:vertical-rl]">书架</span>
        </aside>
      ) : (
        <>
          <aside className="flex min-h-0 shrink-0 flex-col bg-surface" style={{ width: layout.shelfW }}>
            <Bookshelf
              stories={e.stories}
              curId={e.curId}
              onSelect={e.selectStory}
              onCreate={e.createStory}
              onDelete={e.removeStory}
              onCollapse={layout.toggleCollapsed}
            />
          </aside>
          <Divider which="shelf" onStart={layout.startDrag} />
        </>
      )}

      {/* ── 中:阅读列(主角)── */}
      <main className="flex min-h-0 min-w-0 flex-col" style={{ flexGrow: layout.midSplit, flexBasis: 0 }}>
        <header className="flex items-baseline gap-3 border-b border-line px-10 py-4">
          <span className="font-serif text-[15px] font-medium tracking-tight text-accent-ink">
            <span className="text-accent">❡</span> vore-tree
          </span>
          {e.curId ? (
            <>
              <span className="truncate font-serif text-[19px] text-ink">{e.title}</span>
              <div className="ml-auto flex items-center gap-4">
                <span className="shrink-0 font-mono text-[11px] text-ink-faint">
                  {chapters ? `${chapters} 拍已写就` : "尚未落笔"}
                </span>
                <button
                  onClick={e.openScope}
                  className="relative flex shrink-0 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
                  title="摊开本轮三段式内部流:看进度、看每个 agent 的上下文、回退/重试/副本"
                >
                  <span className="text-accent">⊞</span> 导演工作台
                  {e.turnStreaming && (
                    <span className="absolute -right-1 -top-1 h-2 w-2 rounded-full bg-accent ring-2 ring-paper breathe" />
                  )}
                </button>
                <button
                  onClick={e.openSettings}
                  className="flex shrink-0 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-2.5 py-1 text-[12px] text-ink-soft transition hover:border-accent hover:text-accent-ink"
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
            <ReadingColumn turns={e.turns} onTurnClick={focusTurnOnMap} onDismissFailure={e.dismissFailure} />
            {inputFocused && <OptionChips options={e.options} disabled={e.turnStreaming} onPick={pickOption} />}
            <Composer
              disabled={!e.curId}
              streaming={e.turnStreaming}
              onSubmit={e.submitTurn}
              prefillText={prefill?.text}
              prefillKey={prefill?.key}
              onFocusChange={setInputFocused}
            />
          </>
        ) : (
          <EmptyReading />
        )}
      </main>

      <Divider which="mid" onStart={layout.startDrag} />

      {/* ── 地图:与对话左右并列的常驻列(可拖宽)── */}
      <section className="flex min-h-0 min-w-0 flex-col bg-paper" style={{ flexGrow: 1 - layout.midSplit, flexBasis: 0 }}>
        {e.curId ? (
          <SceneMap storyId={e.curId} onJumpToTurn={jumpToTurn} refreshKey={mapRefreshKey} focusReq={focusReq} />
        ) : (
          <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] text-ink-faint">
            选一卷故事,这里会画出场景地图。
          </div>
        )}
      </section>

      <Divider which="right" onStart={layout.startDrag} />

      {/* ── 右坞:此刻 / 场景与图 / 绘图台(选项卡,可拖宽)── */}
      <aside className="flex min-h-0 shrink-0 flex-col border-l border-line bg-surface" style={{ width: layout.rightW }}>
        {e.curId ? (
          <>
            <div className="flex shrink-0 border-b border-line">
              {tabs.map((t) => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`relative flex-1 px-2 py-2.5 text-[12.5px] font-medium transition-colors ${
                    tab === t.key ? "text-accent-ink" : "text-ink-faint hover:text-ink-soft"
                  }`}
                >
                  {t.label}
                  {t.badge ? (
                    <span className="ml-1 rounded-full bg-accent-soft px-1.5 py-px font-mono text-[10px] text-accent-ink">
                      {t.badge}
                    </span>
                  ) : null}
                  {tab === t.key && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-accent" />}
                </button>
              ))}
            </div>

            {/* 各面板始终挂载、按 tab 切换可见(display 切换)→ 切 tab 不丢面板内交互/滚动/进行中状态 */}
            <div className={tab === "now" ? "min-h-0 flex-1 overflow-y-auto" : "hidden"}>
              <StatePanel blackboard={e.blackboard} />
            </div>
            <div className={tab === "scenes" ? "min-h-0 flex-1 overflow-y-auto" : "hidden"}>
              <ScenesPanel
                blackboard={e.blackboard}
                scenesImages={e.scenesImages}
                scenesDrafts={e.scenesDrafts}
                supersededImages={e.supersededImages}
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
                onSubstitute={e.substituteDraft}
                onDismiss={e.dropDraft}
              />
            </div>
            <div className={tab === "draw" ? "min-h-0 flex-1 overflow-y-auto" : "hidden"}>
              <DrawDeck
                storyId={e.curId}
                drawsVersion={e.drawsVersion}
                onReload={e.reloadScope}
                onWriting={e.onWriting}
                onGenerating={e.onGenerating}
              />
            </div>
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
          liveError={e.liveError}
          dismissFailure={e.dismissFailure}
          writingIds={e.writingIds}
          generatingIds={e.generatingIds}
          onWriting={e.onWriting}
          onGenerating={e.onGenerating}
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

/** 竖直可拖分隔线 —— 替代原先的固定边框,按下后由 useLayout 接管拖动调宽。 */
function Divider({ which, onStart }: { which: DragWhich; onStart: (w: DragWhich, e: React.PointerEvent) => void }) {
  return (
    <div
      onPointerDown={(e) => onStart(which, e)}
      title="拖动调整宽度"
      className="group relative w-1.5 shrink-0 cursor-col-resize bg-line transition-colors hover:bg-accent/30"
    >
      <span className="pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-line-strong/50 transition-colors group-hover:bg-accent" />
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
