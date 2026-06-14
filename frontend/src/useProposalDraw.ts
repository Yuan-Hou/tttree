import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import type { ProposalDraw } from "./types";

/** 拉取某绘图提案的写稿/画图展示数据(写稿输入+输出、两类参考图来源、已出的图)。 */
export function useProposalDraw(storyId: string, pid: number) {
  const [data, setData] = useState<ProposalDraw | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.getProposalDraw(storyId, pid));
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [storyId, pid]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, loading, reload, setData };
}
