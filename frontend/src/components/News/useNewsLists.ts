"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type NewsWatchList } from "@/lib/api";

/**
 * The news watch lists (desk `news_lists.py`). On the scanner server they live on the desk and are read and
 * added to over the link; on the desk (and the backtest server) through the desk's own routes.
 */
export function useNewsLists() {
  const [lists, setLists] = useState<NewsWatchList[]>([]);
  const [onScanner, setOnScanner] = useState(false);
  const [deskUrl, setDeskUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const role = await api.serverRole().catch(() => null);
      const scanner = role?.role === "scanner";
      setOnScanner(scanner);
      setDeskUrl(scanner ? role?.desk_url ?? null : null);
      const res = scanner ? await api.deskNewsLists() : await api.newsLists();
      setLists(res.lists);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load the watch lists");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Add stocks to a list (`listId`), or to a new list called `newName`. */
  const addTo = useCallback(
    async (symbols: string[], listId: number | null, newName: string | null): Promise<NewsWatchList> => {
      let row: NewsWatchList;
      if (onScanner) row = await api.deskNewsListAdd(symbols, listId, newName);
      else if (listId) row = await api.updateNewsList(listId, { add: symbols });
      else row = await api.createNewsList(newName || "Watch list", symbols);
      await reload();
      return row;
    },
    [onScanner, reload]
  );

  /** The News page for these stocks: the desk's own page when on the scanner (its news watcher runs there). */
  const newsPageUrl = useCallback(
    (symbols: string[]) => {
      const qs = symbols.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
      const base = onScanner && deskUrl ? deskUrl.replace(/\/$/, "") : "";
      return `${base}/news/${qs}`;
    },
    [onScanner, deskUrl]
  );

  return { lists, error, reload, addTo, onScanner, newsPageUrl };
}
