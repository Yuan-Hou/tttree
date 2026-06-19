import { StrictMode, useCallback, useMemo, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { ReadingColumn } from "./components/ReadingColumn";
import { SceneMap } from "./components/SceneMap";
import { LightboxProvider } from "./components/Lightbox";
import { ToastProvider } from "./components/Toast";
import { snapshotToTurns } from "./snapshot";
import { usePortrait } from "./usePortrait";
import { seedPositions, type NodePositions } from "./nodeLayout";
import type { SceneMap as SceneMapData, Snapshot } from "./types";

/** 冻结的故事快照(导出时由后端注入)。图片路径均已内联为 data: URI;
 *  layout = 导出时作者整理好的地图节点坐标(按 slug)。 */
interface ExportData {
  snapshot: Snapshot;
  sceneMap: SceneMapData;
  layout?: NodePositions;
}
declare global {
  interface Window {
    __VORE_EXPORT__?: ExportData;
  }
}

const jsonResponse = (data: unknown) =>
  new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });

/** 把查看器内组件原样发出的 GET 拦在前端:scene-map / snapshot 用内联数据应答,其余放行。
 *  好处:SceneMap 等组件零改动复用,将来地图新增的取数也只需进冻结快照即可。 */
function installFetchShim({ snapshot, sceneMap }: ExportData) {
  const realFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.includes("/scene-map")) return Promise.resolve(jsonResponse(sceneMap));
    if (url.endsWith("/snapshot")) return Promise.resolve(jsonResponse(snapshot));
    return realFetch(input as RequestInfo, init);
  };
}

function Viewer({ snapshot, sceneMap }: ExportData) {
  const storyId = snapshot.story_id;
  const title = snapshot.title || sceneMap.current_scene || "vore-tree";
  const turns = useMemo(() => snapshotToTurns(snapshot), [snapshot]);
  const portrait = usePortrait();
  const [mapOpen, setMapOpen] = useState(true);

  // 点对话 → 地图聚焦该轮落点 + 翻图(与实时应用同款联动)。nonce 保证连点重触发。
  const [focusReq, setFocusReq] = useState<{ turnIndex: number; nonce: number } | null>(null);
  const focusTurnOnMap = useCallback(
    (turnIndex: number) => setFocusReq({ turnIndex, nonce: Date.now() }),
    [],
  );
  // 点地图实线 → 滚动对话到对应轮。
  const jumpToTurn = useCallback((turnIndex: number) => {
    document.getElementById(`turn-${turnIndex}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // 竖屏:地图在上、可折叠(展开 ~42vh);对话在下为主体。横屏:左右并列。
  const mapStyle = portrait
    ? { height: mapOpen ? "42vh" : 0, flex: "none" as const }
    : { flex: 1, minWidth: 0 };
  const readingStyle = { flex: 1, minHeight: 0, minWidth: 0 } as const;

  return (
    <div className="flex h-full w-full flex-col bg-paper text-ink">
      <header className="flex shrink-0 items-baseline gap-3 border-b border-line px-5 py-3 sm:px-8">
        <span className="font-serif text-[14px] font-medium tracking-tight text-accent-ink">
          <span className="text-accent">❡</span> vore-tree
        </span>
        <span className="truncate font-serif text-[17px] text-ink">{title}</span>
        <span className="ml-auto shrink-0 font-mono text-[11px] text-ink-faint">
          {turns.length ? `${turns.length} 拍` : ""}
        </span>
      </header>

      <div
        className="flex min-h-0 flex-1"
        style={{ flexDirection: portrait ? "column" : "row" }}
      >
        {/* 地图:横屏在右(order 后),竖屏在上(order 前)。
            必须是 flex 列容器 —— SceneMap 根用 flex-1 撑高,React Flow 需要确定高度的父级 */}
        <section
          className="relative flex min-h-0 min-w-0 flex-col overflow-hidden bg-paper transition-[height] duration-300"
          style={{ ...mapStyle, order: portrait ? 0 : 1 }}
        >
          {(!portrait || mapOpen) && (
            <SceneMap storyId={storyId} onJumpToTurn={jumpToTurn} focusReq={focusReq} />
          )}
        </section>

        {/* 竖屏折叠条:横屏隐藏 */}
        {portrait && (
          <button
            onClick={() => setMapOpen((v) => !v)}
            className="flex shrink-0 items-center justify-center gap-1.5 border-y border-line bg-surface py-1.5 text-[11px] text-ink-soft"
            style={{ order: 0 }}
          >
            <span className="text-accent">{mapOpen ? "▴" : "▾"}</span>
            {mapOpen ? "收起地图" : "展开地图"}
          </button>
        )}

        {/* 对话:横屏在左(order 前),竖屏在下(order 后) */}
        <main className="flex min-h-0 flex-col" style={{ ...readingStyle, order: portrait ? 1 : 0 }}>
          <ReadingColumn turns={turns} onTurnClick={focusTurnOnMap} />
        </main>
      </div>
    </div>
  );
}

function NoData() {
  return (
    <div className="flex h-full w-full items-center justify-center px-8 text-center text-[13px] text-ink-faint">
      没有可显示的故事数据。这是 vore-tree 的导出查看器,需由后端导出接口注入冻结快照后才能浏览。
    </div>
  );
}

const root = createRoot(document.getElementById("root")!);
const mount = (node: ReactNode) =>
  root.render(
    <StrictMode>
      <ToastProvider>
        <LightboxProvider>{node}</LightboxProvider>
      </ToastProvider>
    </StrictMode>,
  );

const data = window.__VORE_EXPORT__;
if (data) {
  installFetchShim(data); // 一次性安装:在任何组件取数之前
  // 把作者整理好的地图布局种进 localStorage(SceneMap 挂载时会自然读取),在挂载前完成。
  if (data.layout) seedPositions(`${data.snapshot.story_id}.map`, data.layout);
  mount(<Viewer {...data} />);
} else if (import.meta.env.DEV && new URLSearchParams(location.search).get("story")) {
  // 开发期预览:无注入数据时,按 ?story=ID 直接向后端(经 Vite proxy)取实时快照,免装 shim。
  const id = new URLSearchParams(location.search).get("story")!;
  Promise.all([
    fetch(`/story/${id}/snapshot`).then((r) => r.json()),
    fetch(`/story/${id}/scene-map`).then((r) => r.json()),
  ])
    .then(([snapshot, sceneMap]) => mount(<Viewer snapshot={snapshot} sceneMap={sceneMap} />))
    .catch(() => mount(<NoData />));
} else {
  mount(<NoData />);
}
