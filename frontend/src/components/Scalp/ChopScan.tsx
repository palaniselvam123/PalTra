"use client";

import { StopScan } from "@/components/Scalp/StopScan";
import { ScanListPicker, cleanLists, resolveScanSymbols, type ScanList } from "@/components/Scalp/ScanLists";
import { Explain } from "@/components/ui/Explain";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { lastScanText } from "@/lib/lastScan";
import { Activity, Loader2, RefreshCw } from "lucide-react";
import { SmaPick } from "@/components/Scalp/SmaPick";
import { DESK_SMA, SCAN_SIZES, cleanSma, sizeLabel, sizeShort, sizeWords, type ScanSma } from "@/lib/scanSizes";
import { smaApi, type ChopAuto, type ChopAutoUpdate, type ChopCandles, type ChopScan as ChopScanData, type ChopPhaseSide, type ChopScanRow, type ChopScore, type ChopTrendCheck, type CrossScanMinutes, type TrendAlerts } from "@/lib/smaApi";
import { FilterField, FilterGroup, FilterRow, NumberFilter, SelectFilter, TextFilter, Toggle, matchesText } from "@/components/ui/tableTools";
import { SortTh, useSort } from "@/components/Terminal/sortable";
import { Pager, usePaged } from "@/components/ui/pagination";
import { useFirstSeen } from "@/components/Scalp/useFirstSeen";
import { ColumnHint, inOrder, useColumnOrder } from "@/components/ui/columnOrder";
import type { OnScanStatus } from "@/components/Scalp/scanStatus";
import { GrowwLink } from "@/components/ui/growwLink";
import { TickBar, TickCell, TickHead, useTicks } from "@/components/Scalp/TickArm";
import { ageText, rankTrends, trendRead, type RankedTrend, type TrendRead } from "@/lib/trendRank";
import { CANDLE_KIND_OPTIONS, SCORE_OPTIONS, TREND_DIRS, TREND_KINDS, asCandleKind, candleWords, CPR_NARROW, CPR_WIDE, DAY_LEVEL_OPTIONS, STACK3_OPTIONS, VS_INDEX_OPTIONS, chopPasses, colourRead, crossRulesFor, modeWords, trendKindsFor, inPriceRange, isCrossRule, isStrong, type CrossRule, type DayLevelFilter, type ScoreFilter, type Stack3Filter, type VsIndexFilter } from "@/lib/chopFilter";
import { GuideBar } from "./GuideBar";
import { NewTag } from "@/components/ui/NewTag";
import { api, type ActiveStock } from "@/lib/api";

const CANDLE_CHOICES: CrossScanMinutes[] = [...SCAN_SIZES];

type TrendDir = (typeof TREND_DIRS)[number][0];
type TrendKind = (typeof TREND_KINDS)[number][0];

const POLL_MS = 3_000;
// Auto re-scan period in milliseconds (30 min). GitHub-cron-style, best-effort; the browser
// has to be awake. On mobile a backgrounded tab may skip ticks.
// While the server's re-scan is on, the card fetches the newest result this often.
const AUTO_POLL_MS = 60_000;
const EVERY_CHOICES = [1, 2, 3, 4, 5, 10, 15, 20, 30, 45, 60];
// "Skip today's first candles" choices (0 = none); the server allows up to 12.
const SKIP_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 12];
const QUIET_STORE = "sma.chop.quiet"; // localStorage key for the alert settings

type Props = {
  armed: Map<string, string[]>;
  arming: string | null;
  onArm: (symbol: string) => void;
  /** Arm several ticked stocks on one bot at once (the live card only; a saved scan has no ticks). */
  onArmMany?: (symbols: string[]) => void;
  /** Scans are paused from the top of the Scalp page. */
  paused?: boolean;
  /** Tells the page's tab bar when this scan last ran. */
  onStatus?: OnScanStatus;
  /** A saved scan from the History tab: shown read-only exactly as the page showed it (no scanning, no alerts). */
  saved?: ChopScanData;
  /** "10:04 IST · Wed, 14 Oct" for a saved scan's title. */
  savedTitle?: string;
};

const fmtGap = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`);

type SortKey = "symbol" | "ltp" | "score" | "first" | "rank" | "age" | "gap" | "three" | "vsIndex" | "cpr" | "trend" | "phase" | "dayMove" | "buyers" | "crosses" | "since" | "avgGap" | "avgMove" | "speed" | "volume";
type Col = SortKey | "sizes" | "arm";
const COLUMNS: readonly Col[] = ["symbol", "ltp", "score", "first", "rank", "age", "gap", "three", "trend", "phase", "dayMove", "vsIndex", "cpr", "buyers", "sizes", "crosses", "since", "avgGap", "avgMove", "speed", "volume", "arm"];

const SCORE_LABEL: Record<ChopScore, string> = { TRENDING: "Trending", MIXED: "Mixed", CHOPPY: "Choppy" };
const SCORE_CLASS: Record<ChopScore, string> = {
  TRENDING: "bg-accentSky/10 text-accentSky ring-accentSky/40",
  MIXED: "bg-slate-700/40 text-slate-300 ring-slate-500/40",
  CHOPPY: "bg-accentViolet/10 text-accentViolet ring-accentViolet/40",
};

// What each strong-trend check means. Before 10:00 (morning mode) the server judges today's
// candles only, with the seven checks below; from 10:00 (midday) the 12-candle rules apply.
// The server's own per-check note (`trend_notes`) wins when it is sent.
const MIDDAY_TEXT: Partial<Record<ChopTrendCheck, string>> = {
  stack: "Price beyond the fast SMA, fast beyond the slow SMA",
  vwap: "Price and the slow SMA on the trend side of VWAP",
  slope: "Fast and slow SMA both sloping with the trend",
  fan: "SMAs fanned apart (gap ≥ 0.10%) and not closing up",
  hold: "≥ 75% of the last 12 closes on the trend side of the fast SMA",
  structure: "Lower highs and lows (bearish) / higher highs and lows (bullish)",
  run: "No SMA cross in the last 12 candles",
};
const MORNING_TEXT: Partial<Record<ChopTrendCheck, string>> = {
  stack: "Price beyond the fast SMA, fast beyond the slow SMA (today)",
  vwap: "Price and the fast SMA on the trend side of today's VWAP",
  slope: "Fast SMA moving with the trend since the open",
  fan: "SMAs at least 0.10% apart on the trend side",
  hold: "Closes on the trend side of the fast SMA today (all of them to 09:30, then 75%)",
  run: "A fresh cross today is allowed; otherwise the lines are on the trend side since the open",
  volume: "Volume since 09:15 at least 1.5× the same minutes on earlier sessions",
};
// Heikin Ashi candles (backend ha_trend.py): the trend is read from the candles themselves, no SMA.
const HA_TEXT: Partial<Record<ChopTrendCheck, string>> = {
  streak: "At least 3 Heikin Ashi candles of the trend's colour in a row",
  nowick: "Most of the last candles have no wick against the trend",
  body: "The last body is not much smaller than the run's average",
  structure: "Higher lows (green run) / lower highs (red run)",
  vwap: "The real price on the trend's side of today's VWAP",
  move: "The run has moved at least 1 ATR",
  clean: "No doji in the last 3 candles",
};
const MIDDAY_ORDER = Object.keys(MIDDAY_TEXT) as ChopTrendCheck[];
const MORNING_ORDER = Object.keys(MORNING_TEXT) as ChopTrendCheck[];
const HA_ORDER = Object.keys(HA_TEXT) as ChopTrendCheck[];
// The same colour checks read on the real candles ("Normal (colours)").
const COLOUR_TEXT: Partial<Record<ChopTrendCheck, string>> = Object.fromEntries(
  Object.entries(HA_TEXT).map(([k, v]) => [k, (v ?? "").replace("Heikin Ashi candles", "candles")])
) as Partial<Record<ChopTrendCheck, string>>;
const orderFor = (r: ChopScanRow) =>
  r.trend_mode === "HEIKIN_ASHI" || r.trend_mode === "COLOUR" ? HA_ORDER : r.trend_mode === "MORNING" ? MORNING_ORDER : MIDDAY_ORDER;
const textsFor = (r: ChopScanRow) =>
  r.trend_mode === "HEIKIN_ASHI" ? HA_TEXT : r.trend_mode === "COLOUR" ? COLOUR_TEXT : r.trend_mode === "MORNING" ? MORNING_TEXT : MIDDAY_TEXT;


const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
// A trend's start: "10:20" today, "Mon 14:05" on an earlier day (IST).
function sinceText(ts: number): string {
  const d = new Date(ts * 1000);
  const day = (x: Date) => x.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  const time = d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  if (day(d) === day(new Date())) return time;
  return `${d.toLocaleDateString("en-IN", { weekday: "short", timeZone: "Asia/Kolkata" })} ${time}`;
}
const num = (v: number | null | undefined, digits = 2) => (v == null ? "—" : v.toFixed(digits));

function shares(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1e7) return `${(v / 1e7).toFixed(1)} Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(1)} L`;
  return Math.round(v).toLocaleString("en-IN");
}

/**
 * How choppy or trending each F&O stock has looked this session. Reads today's
 * SMA crosses on the chosen candle and labels each row Trending / Mixed /
 * Choppy. Information only: it places no order, and Arm only puts a stock on a
 * bot's Trade list.
 */
export function ChopScan({ armed, arming, onArm, onArmMany, paused = false, onStatus, saved, savedTitle }: Props) {
  const cols = useColumnOrder<Col>("scalp.cols.trend", COLUMNS);
  const [data, setData] = useState<ChopScanData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [minutes, setMinutes] = useState<CrossScanMinutes>(5);
  // Which candles the scan reads: the real ones, or Heikin Ashi (smoother colours; prices shown stay real).
  const [candles, setCandles] = useState<ChopCandles>("NORMAL");
  // Which moving averages the scan reads (owner's request: 20 / 50 or his own numbers); 0 / 0 = Bot 1's own.
  // The colour reads use no SMA, so they always scan with Bot 1's.
  const [sma, setSma] = useState<ScanSma>(DESK_SMA);
  const scanSma = colourRead(candles) ? DESK_SMA : sma;
  // Today's first N candles left out of the trend read (owner's request: big opening candles made a sideways stock
  // read as a trend). 0 = none.
  const [skipFirst, setSkipFirst] = useState(0);
  // A third, longer SMA (owner's request: 9 / 21 / 50 with the gaps widening). 0 = off. Not for the colour reads.
  const [third, setThird] = useState(0);
  const [thirdTyped, setThirdTyped] = useState(false);
  const slowInUse = scanSma.slow || 21;
  const scanThird = colourRead(candles) || third <= slowInUse || third > 200 ? 0 : third;
  const [stack3, setStack3] = useState<Stack3Filter>("ANY");
  const [minWiden, setMinWiden] = useState(0);
  // Against the market and the day's levels (the vs Nifty and CPR columns).
  const [vsIndex, setVsIndex] = useState<VsIndexFilter>("ANY");
  const [minVsIndex, setMinVsIndex] = useState(0);
  const [maxCpr, setMaxCpr] = useState(0);
  const [dayLevel, setDayLevel] = useState<DayLevelFilter>("ANY");
  const [score, setScore] = useState<ScoreFilter>("ALL");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  // Which stocks to scan (Armed / F&O / Nifty 50 / Nifty 100 / Nifty Bank, joined). Default: armed stocks only.
  const [lists, setLists] = useState<ScanList[]>(["armed"]);
  const armedOnly = lists.length === 1 && lists[0] === "armed";
  // Alert settings. Default: 60 min of quiet, Telegram ping on.
  const [quietMin, setQuietMin] = useState(60);
  const [alertOnQuiet, setAlertOnQuiet] = useState(true);
  // The server's own re-scan (chop_auto.py): on/off and every N minutes live on the server, not in this browser.
  const [auto, setAuto] = useState<ChopAuto | null>(null);
  // True once the server's last result (and its auto settings) has loaded, so the card does not overwrite them.
  const [synced, setSynced] = useState(false);
  // Fast movers only: |speed| over the last 10 minutes at least this many % per minute (0 = off).
  const [minSpeed, setMinSpeed] = useState(0);
  // "No cross today on": keep stocks whose SMAs have not crossed once today on every ticked candle size.
  const [noCrossOn, setNoCrossOn] = useState<CrossScanMinutes[]>([]);
  // How many crosses today the ticked sizes must have (CROSS_RULES: none, exactly N, at most N, N or more, one up / down).
  const [crossCount, setCrossCount] = useState<CrossRule>("0");
  // Trend on: every ticked candle size must point the chosen way (none ticked = off).
  const [trendOn, setTrendOn] = useState<CrossScanMinutes[]>([]);
  const [trendDir, setTrendDir] = useState<TrendDir>("UP");
  const [trendKind, setTrendKind] = useState<TrendKind>("SIDE");
  // With Trend on: how old the aligned trend may be (market minutes, 0 = any), how wide its SMA gap must be,
  // and whether to hide a trend whose gap has shrunk to under half its widest (fading).
  const [maxAge, setMaxAge] = useState(0);
  const [minGap, setMinGap] = useState(0);
  const [hideFading, setHideFading] = useState(false);
  // The server's trend-rank alert (Telegram / call for the top ranks after each pass).
  const [trendAlert, setTrendAlert] = useState<TrendAlerts | null>(null);
  // Price range in ₹ (0 = no bound): filters the table, the quiet pop-up and the Telegram alert.
  const [priceMin, setPriceMin] = useState(0);
  const [priceMax, setPriceMax] = useState(0);
  // "I have shown this stock as quiet already" — stops the popup repeating each tick while it's open.
  const seenRef = useRef<Set<string>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  // Remember the alert settings between visits.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(QUIET_STORE);
      if (raw) {
        const saved = JSON.parse(raw);
        setLists(cleanLists(saved.lists, typeof saved.armedOnly === "boolean" && !saved.armedOnly ? ["fno"] : ["armed"]));
        if (typeof saved.quietMin === "number") setQuietMin(saved.quietMin);
        if (typeof saved.alertOnQuiet === "boolean") setAlertOnQuiet(saved.alertOnQuiet);
        if (typeof saved.minSpeed === "number") setMinSpeed(saved.minSpeed);
        if (isCrossRule(String(saved.crossCount))) setCrossCount(String(saved.crossCount) as CrossRule);
        if (typeof saved.priceMin === "number") setPriceMin(saved.priceMin);
        if (typeof saved.priceMax === "number") setPriceMax(saved.priceMax);
        if (Array.isArray(saved.noCrossOn))
          setNoCrossOn(saved.noCrossOn.filter((m: unknown) => CANDLE_CHOICES.includes(m as CrossScanMinutes)));
        if (typeof saved.score === "string") setScore(saved.score as ScoreFilter);
        if (Array.isArray(saved.trendOn))
          setTrendOn(saved.trendOn.filter((m: unknown) => CANDLE_CHOICES.includes(m as CrossScanMinutes)));
        if (TREND_DIRS.some(([v]) => v === saved.trendDir)) setTrendDir(saved.trendDir as TrendDir);
        if (TREND_KINDS.some(([v]) => v === saved.trendKind)) setTrendKind(saved.trendKind as TrendKind);
        if (typeof saved.maxAge === "number") setMaxAge(saved.maxAge);
        if (typeof saved.minGap === "number") setMinGap(saved.minGap);
        if (typeof saved.hideFading === "boolean") setHideFading(saved.hideFading);
      }
    } catch {
      /* private mode */
    }
  }, []);
  useEffect(() => {
    if (saved) return; // a saved scan's view never changes the live card's settings
    try {
      localStorage.setItem(QUIET_STORE, JSON.stringify({ lists, armedOnly, quietMin, alertOnQuiet, minSpeed, score, noCrossOn, crossCount, priceMin, priceMax, trendOn, trendDir, trendKind, maxAge, minGap, hideFading })
      );
    } catch {
      /* private mode */
    }
  }, [saved, lists, armedOnly, quietMin, alertOnQuiet, minSpeed, score, noCrossOn, crossCount, priceMin, priceMax, trendOn, trendDir, trendKind, maxAge, minGap, hideFading]);

  useEffect(() => {
    if (saved) return;
    smaApi
      .trendAlerts()
      .then(setTrendAlert)
      .catch(() => {
        /* an older server without trend alerts */
      });
  }, [saved]);
  const changeTrendAlert = (change: Partial<Omit<TrendAlerts, "recent">>) => {
    setError(null);
    smaApi
      .saveTrendAlerts(change)
      .then(setTrendAlert)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not save the trend alert"));
  };
  // The alert follows the Trend on filter and the age / gap limits set here.
  const alertFromFilters = () => ({
    sizes: trendOn,
    direction: trendDir,
    kind: trendKind,
    max_age_min: maxAge,
    min_gap_pct: minGap,
    skip_fading: hideFading,
  });
  const alertMatchesFilters =
    !!trendAlert &&
    trendAlert.sizes.join(",") === trendOn.join(",") &&
    trendAlert.direction === trendDir &&
    trendAlert.kind === trendKind &&
    trendAlert.max_age_min === maxAge &&
    Math.abs(trendAlert.min_gap_pct - minGap) < 1e-9 &&
    trendAlert.skip_fading === hideFading;

  const start = useCallback(
    async (force = false) => {
      setBusy(true);
      setError(null);
      try {
        const symbols = await resolveScanSymbols(lists, armed.keys());
        setData(
          await smaApi.chopScanStart(symbols, minutes, force, {
            quiet_min: quietMin,
            alert_on_quiet: alertOnQuiet,
            price_min: priceMin,
            price_max: priceMax,
            candles,
            skip_candles: skipFirst,
            sma_fast: scanSma.fast,
            sma_slow: scanSma.slow,
            sma_third: scanThird,
          })
        );
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Could not start the scan");
      } finally {
        setBusy(false);
      }
    },
    [minutes, candles, skipFirst, scanSma.fast, scanSma.slow, scanThird, armed, lists, quietMin, alertOnQuiet, priceMin, priceMax]
  );

  // The server re-scans by itself (no browser needed). Tell it the card's choices, so its scans match.
  useEffect(() => {
    if (!synced || saved) return;
    const id = setTimeout(() => {
      smaApi
        .chopScanAuto({
          minutes,
          candles,
          skip_candles: skipFirst,
          sma_fast: sma.fast,
          sma_slow: sma.slow,
          sma_third: scanThird,
          armed_only: armedOnly,
          lists,
          quiet_min: quietMin,
          alert_on_quiet: alertOnQuiet,
          price_min: priceMin,
          price_max: priceMax,
        })
        .then(setAuto)
        .catch(() => {
          /* an older server without the re-scan: the card still scans on Scan now */
        });
    }, 800);
    return () => clearTimeout(id);
  }, [synced, saved, minutes, candles, skipFirst, sma.fast, sma.slow, scanThird, armedOnly, lists, quietMin, alertOnQuiet, priceMin, priceMax]);

  const changeAuto = (change: ChopAutoUpdate) => {
    setError(null);
    smaApi
      .chopScanAuto(change)
      .then(setAuto)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Could not change the auto re-scan"));
  };

  // While the server's re-scan is on, pick up each new result (and its quiet runners) once a minute.
  useEffect(() => {
    if (saved || !auto?.on || paused) return;
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.hidden) return;
      smaApi
        .chopScan()
        .then((d) => {
          setData(d);
          if (d.auto) setAuto(d.auto);
        })
        .catch(() => {});
    }, AUTO_POLL_MS);
    return () => clearInterval(id);
  }, [saved, auto?.on, paused]);

  // When a result comes back, pop a toast for quiet runners we haven't highlighted yet.
  useEffect(() => {
    if (saved || !data?.rows) return;
    const quiet = data.rows.filter((r) => r.quiet_runner && inPriceRange(r.ltp, priceMin, priceMax));
    if (quiet.length === 0) return;
    const fresh = quiet.filter((r) => !seenRef.current.has(r.symbol));
    if (fresh.length === 0) return;
    fresh.forEach((r) => seenRef.current.add(r.symbol));
    setToast(
      fresh.length === 1
        ? `${fresh[0].symbol} is quiet (possible strong ${fresh[0].last_cross_direction === "BULLISH" ? "bullish" : fresh[0].last_cross_direction === "BEARISH" ? "bearish" : "trend"}). Review.`
        : `${fresh.length} stocks are quiet: ${fresh
            .slice(0, 8)
            .map((r) => r.symbol)
            .join(", ")}${fresh.length > 8 ? ` and ${fresh.length - 8} more` : ""}. Review.${
            fresh.length > 30 ? " That is most of the list: raise \"Alert when quiet for\" to 30-60 min." : ""
          }`
    );
  }, [saved, data?.rows, priceMin, priceMax]);

  // The server holds one result. Opening the page shows it — a scan starts only on Scan now.
  // A saved scan: its own rows and candle, nothing fetched.
  useEffect(() => {
    if (!saved) return;
    setData(saved);
    setMinutes(saved.minutes);
    setCandles(asCandleKind(saved.candles));
    setSkipFirst(Number(saved.skip_candles ?? 0) || 0);
  }, [saved]);

  useEffect(() => {
    if (saved) return;
    let live = true;
    smaApi
      .chopScan()
      .then((d) => {
        if (!live) return;
        setData(d);
        if (d.auto) {
          setAuto(d.auto);
          setMinutes(d.auto.minutes);
          setCandles(asCandleKind(d.auto.candles));
          setSkipFirst(Number(d.auto.skip_candles ?? 0) || 0);
          setSma(cleanSma(d.auto.sma_fast, d.auto.sma_slow));
          setThird(Number(d.auto.sma_third ?? 0) || 0);
        }
      })
      .catch(() => {
        /* the terminal is not answering: the page shows "No scan yet" and Scan now still works */
      })
      .finally(() => live && setSynced(true));
    return () => {
      live = false;
    };
  }, [saved]);

  // While a pass runs, watch its progress.
  useEffect(() => {
    if (!data?.running) return;
    const id = setInterval(() => {
      smaApi
        .chopScan()
        .then(setData)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Lost the scan"));
    }, POLL_MS);
    return () => clearInterval(id);
  }, [data?.running]);

  const scannedThisCandle =
    data?.minutes === minutes &&
    (data?.candles ?? "NORMAL") === candles &&
    (!scanSma.fast || (data?.sma_fast === scanSma.fast && data?.sma_slow === scanSma.slow));
  // The rows on screen carry the three-SMA read (the scan had a third SMA).
  const thirdRead = Number(data?.sma_third ?? data?.rows?.find((r) => r.sma_third)?.sma_third ?? 0) || 0;
  // The rows on screen were read by colour (Heikin Ashi, or the real candles' colours): the trend comes from the
  // candles (ha_trend), not the SMAs. The filters follow the Candles menu, the columns the rows on screen.
  const haRead = colourRead(asCandleKind(data?.candles));
  const rowWords = modeWords(asCandleKind(data?.candles));
  const words = modeWords(candles);
  // Every filter but the stock search: what decides whether a stock is "in the list".
  const passes = useCallback(
    (r: ChopScanRow) =>
      chopPasses(r, { score, minSpeed, priceMin, priceMax, crossCount, noCrossOn, trendOn, trendDir, trendKind, maxAge, minGap, hideFading, stack3: thirdRead ? stack3 : "ANY", minWiden: thirdRead ? minWiden : 0, vsIndex, minVsIndex, maxCpr, dayLevel }),
    [score, minSpeed, noCrossOn, crossCount, priceMin, priceMax, trendOn, trendDir, trendKind, maxAge, minGap, hideFading, stack3, minWiden, thirdRead, vsIndex, minVsIndex, maxCpr, dayLevel]
  );
  const shownRows = useMemo(() => (scannedThisCandle ? (data?.rows ?? []) : []), [data?.rows, scannedThisCandle]);
  const ticks = useTicks();
  const filtered = useMemo(
    () => shownRows.filter((r) => matchesText(query, r.symbol) && ticks.shows(r.symbol) && passes(r)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shownRows, query, passes, ticks.only, ticks.ticked]
  );
  // Trend age, gap and rank (1 = fresh and wide) among the stocks that pass every filter but the stock search.
  // With no Trend on size ticked, the scan's own candle size is read (SMA side, either way), so the columns are never empty.
  const ranks = useMemo(() => {
    const own = data?.minutes;
    if (trendOn.length === 0 && !own) return new Map<string, RankedTrend>();
    const reads = new Map<string, TrendRead>();
    for (const r of shownRows) {
      if (!passes(r)) continue;
      const t = trendOn.length ? trendRead(r, trendOn, trendDir, trendKind) : trendRead(r, [own as number], "SAME", "SIDE");
      if (t) reads.set(r.symbol, t);
    }
    return rankTrends(reads);
  }, [shownRows, passes, trendOn, trendDir, trendKind, data?.minutes]);
  // Buyers vs sellers waiting, from the latest Most active scan (the live card only; read-only).
  const [buyers, setBuyers] = useState<{ at: string | null; by: Map<string, ActiveStock> }>({ at: null, by: new Map() });
  useEffect(() => {
    if (saved) return;
    let live = true;
    api
      .scalpActive({ top: 250 })
      .then((a) => {
        if (live) setBuyers({ at: a.as_of, by: new Map(a.rows.map((x) => [x.symbol, x])) });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [saved, data?.as_of]);
  // When each stock first showed up in this list (these filters) in today's saved scans.
  const firstSeen = useFirstSeen(scannedThisCandle ? data?.as_of : null, data?.minutes, shownRows, passes, "chop", data?.candles ?? "NORMAL", data ? { fast: data.sma_fast, slow: data.sma_slow } : undefined);
  const { sorted: rows, sort, onSort } = useSort<ChopScanRow, SortKey>(filtered, (r, k) => {
    switch (k) {
      case "symbol":
        return r.symbol;
      case "ltp":
        return r.ltp;
      case "first":
        return firstSeen.get(r.symbol) ?? null;
      case "rank":
        return ranks.get(r.symbol)?.rank ?? null;
      case "age":
        return ranks.get(r.symbol)?.ageMin ?? null;
      case "gap":
        return ranks.get(r.symbol)?.gapPct ?? null;
      case "score":
        return r.score === "TRENDING" ? 0 : r.score === "MIXED" ? 1 : 2;
      case "trend":
        return (isStrong(r) ? 1000 : 0) + (r.fresh ? 500 : 0) + (r.trend_score ?? 0);
      case "phase":
        // Strong phases first, longest-running first; then choppy; too early to tell last.
        return r.phase == null || r.phase_early ? null : (r.phase === "CHOP" ? 0 : 1000) + (r.phase_candles ?? 0);
      case "dayMove":
        return r.day_move_pct ?? null;
      case "vsIndex":
        return r.rs_pct ?? null;
      case "cpr":
        return r.cpr_width_pct ?? null;
      case "three":
        // Widening longest first, then stacked longest; not stacked last.
        return r.stack3 ? (r.widen_candles ?? 0) * 1000 + (r.stack3_candles ?? 0) : null;
      case "buyers":
        return buyers.by.get(r.symbol)?.buy_share ?? null;
      case "crosses":
        return r.crosses_today;
      case "since":
        return r.minutes_since_last_cross;
      case "avgGap":
        return r.avg_minutes_between;
      case "avgMove":
        return r.avg_move_pct;
      case "speed":
        return r.speed_pct_per_min;
      case "volume":
        return r.volume;
    }
  });
  const label = data ? `SMA ${data.sma_fast}/${data.sma_slow}` : "SMA";

  useEffect(() => {
    if (saved) return;
    onStatus?.({ when: data?.finished_at ?? data?.as_of ?? null, running: !!data?.running });
  }, [saved, onStatus, data?.finished_at, data?.as_of, data?.running]);

  const paged = usePaged(rows, "scalp.trend.pageSize");
  return (
    <section className="rounded-xl border border-slate-800 bg-card p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Activity size={15} className="text-accentSky" />{" "}
          {saved
            ? `Trend quality — saved scan${savedTitle ? ` · ${savedTitle}` : ""} · ${sizeWords(minutes)} ${candleWords(candles)}candle`
            : `Trend quality — how choppy each stock has looked today on the ${sizeWords(minutes)} ${candleWords(candles)}candle`}
        </h2>
        {!saved && (
        <>
        <button
          type="button"
          onClick={() => void start(true)}
          disabled={busy || data?.running || paused}
          title={paused ? "Scans are paused at the top of the page" : undefined}
          className="ml-auto inline-flex min-h-9 items-center gap-1.5 rounded-md px-3 text-xs font-semibold text-slate-200 ring-1 ring-inset ring-slate-700 hover:bg-white/5 disabled:opacity-50"
        >
          {busy || data?.running ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Scan now
        </button>
        <StopScan
          running={data?.running}
          stop={() => smaApi.scanStop("chop")}
          onStopped={() => void smaApi.chopScan().then(setData).catch(() => undefined)}
        />
        </>
        )}
      </div>
      <GuideBar lesson="scalp-trend-quality" terms={["cross", "chop", "trend", "strong_trend", "fresh_trend", "phase", "vwap", "gap_pct", "speed", "rvol", "arm", "handoff"]} className="mb-2" />
      <Explain
        className="mb-3"
        lead={
          colourRead(candles)
            ? `How choppy or how trending each stock has been today, read from the ${candles === "HA" ? "Heikin Ashi " : ""}candles' colours: how many times they changed colour (dojis skipped), how long the current colour run has lasted, and how far it moved. Few flips and a long run = trending; 5+ flips or flips under 3 candles apart = choppy. Watch only — nothing here places an order.`
            : `How choppy or how trending each stock has been today: how many times its two average lines (${label}) crossed, how long since the last cross, and how far it moved between crosses. Few crosses and a long run = trending; many crosses = choppy. Watch only — nothing here places an order.`
        }
      >
        <b>Trending</b> is a stock with 0 or 1 crosses so far and a long run since the last one (more than an hour). <b>Choppy</b> is 4+ crosses, or crosses coming on average less than 15 minutes apart. <b>Mixed</b> is everything else. <b>Strong trend</b> (bearish ↓ / bullish ↑) is the clean one-way move: price, the fast SMA, the slow SMA and VWAP stacked one way, both SMAs sloping and fanned apart, at least 75% of the last 12 closes on the trend side of the fast SMA (a bounce that fails at the fast SMA still counts), lower highs and lows (higher for bullish) and no cross in the last 12 candles. The first three are required and 6 of the 7 must pass; hover the chip to see which. Add <b>Fast movers</b> to keep only the ones moving quickly right now. Use <b>Scan</b> to pick the stocks: armed stocks, every F&O stock, Nifty 50, Nifty 100 or Nifty Bank (tick several to join them). <b>Price from / to</b> (₹, 0 = any) keeps only stocks priced in that range, in the table, the quiet pop-up and the Telegram alert. <b>Crosses today</b> keeps only the stocks whose SMAs crossed <i>none</i> or <i>exactly 1</i> time today on every candle size you tick (1m, 5m, 15m…): a one-way day, or one turn since the morning and a one-way run after it (the cross time shows as @HH:MM). All sizes are read from the same download. Past crosses do not predict the next cross; the label describes the session so far. <b>Fresh trend</b> (the Fresh ↑ / ↓ chip, and Show → Fresh trend) marks a trend that has only just started: the averages crossed within the last 3 candles, price and the fast average are beyond VWAP, the gap is still widening and volume is at least 1.5× the usual. Strong trend confirms a move that is already old; Fresh trend is the early one. <b>Candles</b>: Normal reads the real candles with the SMA rules above. <b>Heikin Ashi</b> reads the trend from the Heikin Ashi candles themselves and uses no SMA: a stock's side is the colour of its current run, Score counts today's <i>colour flips</i> (Trending = 0-2 flips with a long run since, Choppy = 5+ flips or runs under 3 candles), Strong trend is 6 of 7 candle checks (at least 3 candles of one colour, no wick against the trend, bodies not shrinking, higher lows / lower highs, real price beyond VWAP, a move of at least 1 ATR, no doji lately; the first two and VWAP are required), a phase is 5+ candles of one colour, Trend age is the run's length and the gap column becomes the run's move %. Prices in the table stay real. <b>Normal (colours)</b> reads the real candles the same colour way as Heikin Ashi (colour flips, runs, the same 7 candle checks and phases, no SMA); the filters then count colour flips instead of crosses, Trend on reads the candle colour instead of the SMA side, and the gap filter becomes the run's move. A scan starts only when Scan now is pressed and reads the stocks one at a time (hundreds of downloads).
      </Explain>

      <FilterRow
        status={
          saved
            ? `Saved scan · ${savedTitle ?? ""} · ${saved.rows.length} stocks listed of ${saved.total} scanned`
            : data
              ? data.running
                ? `Scanning ${data.done} of ${data.total}…`
                : lastScanText(data, minutes) ?? "No scan yet"
              : null
        }
      >
        <FilterGroup title="View">
          {!saved && (
            <SelectFilter
              label="Candle"
              value={minutes}
              onChange={(v) => setMinutes(v as CrossScanMinutes)}
              options={CANDLE_CHOICES.map((m) => ({ value: m, label: sizeLabel(m) }))}
              className="w-28"
            />
          )}
          {!saved && !colourRead(candles) ? <SmaPick value={sma} onChange={setSma} /> : null}
          {!saved && !colourRead(candles) ? (
            <div>
              <SelectFilter
                label="Third SMA (longer)"
                value={thirdTyped ? "custom" : String(third)}
                onChange={(v) => {
                  if (v === "custom") {
                    setThirdTyped(true);
                    if (!third) setThird(50);
                    return;
                  }
                  setThirdTyped(false);
                  setThird(Number(v) || 0);
                }}
                options={[
                  { value: "0", label: "Off (two SMAs)" },
                  ...[50, 100, 200].filter((n) => n > slowInUse).map((n) => ({ value: String(n), label: `SMA ${n}` })),
                  { value: "custom", label: "Custom number…" },
                ]}
                title="Add a third, longer moving average to the scan (for example 9 / 21 / 50). Each stock then shows whether the three lines are stacked one way (fast above slow above third, or the mirror) and for how many candles both gaps have been widening. The other columns do not change."
                className="w-40"
              />
              {thirdTyped ? (
                <input
                  type="number"
                  inputMode="numeric"
                  aria-label="Third SMA period"
                  min={slowInUse + 1}
                  max={200}
                  step={1}
                  value={third || ""}
                  onChange={(e) => setThird(Math.max(0, Math.round(Number(e.target.value) || 0)))}
                  className="mt-1 min-h-9 w-20 rounded-md border border-slate-700 bg-surface2 px-2 text-sm tabular-nums text-slate-100"
                />
              ) : null}
              {third > 0 && !scanThird ? (
                <p role="alert" className="mt-1 text-xs text-amber-400">
                  Must be longer than the slow SMA ({slowInUse}) and at most 200.
                </p>
              ) : null}
            </div>
          ) : null}
          {!saved && !colourRead(candles) ? (
            <span className="self-end pb-2" title="New: a third SMA (9 / 21 / 50): see which stocks have the three lines stacked with both gaps widening">
              <NewTag id="tq-three-sma" />
            </span>
          ) : null}
          {!saved ? (
            <span className="self-end pb-2" title="New: 30-minute and 1-hour candles, and the scan's own moving averages (9 / 21, 20 / 50, 50 / 200 or your own two numbers)">
              <NewTag id="scan-sizes-sma" />
            </span>
          ) : null}
          {!saved && (
            <SelectFilter
              label="Candles"
              value={candles}
              onChange={(v) => setCandles(asCandleKind(v))}
              options={CANDLE_KIND_OPTIONS}
              className="w-44"
            />
          )}
          {!saved && (
            <SelectFilter
              label="Skip today's first candles"
              value={String(skipFirst)}
              onChange={(v) => setSkipFirst(Number(v) || 0)}
              options={SKIP_OPTIONS.map((n) => ({ value: String(n), label: n === 0 ? "None" : `First ${n} (${n * minutes} min)` }))}
              title="Leave today's first N candles out of the trend read: crosses, phases, the strong-trend checks and VWAP start after them, and a strong trend must also have moved 1 ATR its way since then. For stocks whose big opening candles made a sideways day look like a trend."
              className="w-48"
            />
          )}
          {!saved ? (
            <span className="self-end pb-2" title="Skip today's first candles: the big opening candles are left out of the trend read (crosses, phases, the strong-trend checks, VWAP), and a strong trend must also have moved 1 ATR its way since them">
              <NewTag id="tq-skip-first" />
            </span>
          ) : null}
          {!saved ? (
            <span className="self-end pb-2" title="Normal (colours): the real candles read by colour, like Heikin Ashi (colour flips and runs, no SMA cross); the filters below change with the choice">
              <NewTag id={candles === "HA" ? "tq-ha-native" : "tq-colour-mode"} />
            </span>
          ) : null}
          <SelectFilter
            label="Show"
            value={score}
            onChange={(v) => setScore(v as ScoreFilter)}
            options={SCORE_OPTIONS}
            className="w-44"
            title={words.score}
          />
          <TextFilter value={query} onChange={setQuery} />
          <NumberFilter
            label="Fast movers: |speed| ≥"
            value={minSpeed}
            onChange={(v) => setMinSpeed(Math.min(1, Math.max(0, Math.round(v * 1000) / 1000)))}
            min={0}
            max={0.3}
            step={0.01}
            suffix="%/min"
            className="w-44"
          />
          <FilterField label={words.crosses}>
            <select
              value={crossCount}
              onChange={(e) => setCrossCount(isCrossRule(e.target.value) ? e.target.value : "0")}
              aria-label={`How many ${words.crossesShort} today`}
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
              title={words.crossesHelp}
            >
              {crossRulesFor(candles).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400">on</span>
            {CANDLE_CHOICES.map((m) => {
              const on = noCrossOn.includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setNoCrossOn((cur) => (on ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b)))}
                  className={clsx(
                    "min-h-9 min-w-10 rounded-md px-2.5 text-xs font-semibold ring-1 ring-inset",
                    on ? "bg-accentSky/15 text-accentSky ring-accentSky/50" : "text-slate-400 ring-slate-700 hover:bg-white/5"
                  )}
                >
                  {sizeShort(m)}
                </button>
              );
            })}
            {noCrossOn.length > 0 && (
              <button type="button" onClick={() => setNoCrossOn([])} className="text-xs text-slate-500 underline-offset-2 hover:underline">
                clear
              </button>
            )}
          </FilterField>
          <FilterField label="Trend on">
            <select
              value={trendDir}
              onChange={(e) => setTrendDir(e.target.value as TrendDir)}
              aria-label="Trend direction on every ticked candle size"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            >
              {TREND_DIRS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              value={trendKind}
              onChange={(e) => setTrendKind(e.target.value as TrendKind)}
              aria-label="How strong the trend must be"
              className="min-h-9 rounded-md border border-slate-700 bg-base px-2 text-sm text-slate-100 outline-none focus:border-slate-500"
              title={words.sideHelp}
            >
              {trendKindsFor(candles).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <span className="text-xs text-slate-400">on</span>
            {CANDLE_CHOICES.map((m) => {
              const on = trendOn.includes(m);
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setTrendOn((cur) => (on ? cur.filter((x) => x !== m) : [...cur, m].sort((a, b) => a - b)))}
                  className={clsx(
                    "min-h-9 min-w-10 rounded-md px-2.5 text-xs font-semibold ring-1 ring-inset",
                    on ? "bg-profit/15 text-profit ring-profit/50" : "text-slate-400 ring-slate-700 hover:bg-white/5"
                  )}
                >
                  {sizeShort(m)}
                </button>
              );
            })}
            {trendOn.length > 0 && (
              <button type="button" onClick={() => setTrendOn([])} className="text-xs text-slate-500 underline-offset-2 hover:underline">
                clear
              </button>
            )}
          </FilterField>
          {trendOn.length > 0 && (
            <>
              <NumberFilter
                label="Trend age at most (0 = any)"
                value={maxAge}
                onChange={(v) => setMaxAge(Math.min(1440, Math.max(0, Math.round(v))))}
                min={0}
                max={240}
                step={5}
                suffix=" min"
                className="w-40"
              />
              <NumberFilter
                label={`${words.gap} at least`}
                value={minGap}
                onChange={(v) => setMinGap(Math.min(5, Math.max(0, Math.round(v * 100) / 100)))}
                min={0}
                max={1}
                step={0.01}
                suffix="%"
                className="w-36"
              />
              <FilterField label="Fading">
                <Toggle
                  label="Hide fading trends"
                  checked={hideFading}
                  onChange={setHideFading}
                  title={words.gapHelp}
                />
              </FilterField>
            </>
          )}
        </FilterGroup>
        <FilterGroup title="Market & levels">
          <SelectFilter
            label="Against Nifty today"
            value={vsIndex}
            onChange={(v) => setVsIndex(v as VsIndexFilter)}
            options={VS_INDEX_OPTIONS}
            title="The stock's move today minus Nifty's. Stronger than Nifty suits buys, weaker suits sells. Needs a scan that could read Nifty"
            className="w-48"
          />
          {vsIndex !== "ANY" ? (
            <NumberFilter
              label="By at least"
              value={minVsIndex}
              onChange={(v) => setMinVsIndex(Math.min(10, Math.max(0, Math.round(v * 10) / 10)))}
              min={0}
              max={3}
              step={0.1}
              suffix="%"
              className="w-32"
            />
          ) : null}
          <NumberFilter
            label="CPR width at most (0 = any)"
            value={maxCpr}
            onChange={(v) => setMaxCpr(Math.min(5, Math.max(0, Math.round(v * 100) / 100)))}
            min={0}
            max={1}
            step={0.05}
            suffix="%"
            className="w-44"
          />
          <SelectFilter
            label="Price is"
            value={dayLevel}
            onChange={(v) => setDayLevel(v as DayLevelFilter)}
            options={DAY_LEVEL_OPTIONS}
            title="Where the last price sits against the Central Pivot Range or yesterday's high / low"
            className="w-52"
          />
          <span className="self-end pb-2">
            <NewTag id="tq-vs-nifty-cpr" />
          </span>
        </FilterGroup>
        {thirdRead ? (
          <FilterGroup title="Three SMAs">
            <SelectFilter
              label={`SMA ${data?.sma_fast} / ${data?.sma_slow} / ${thirdRead}`}
              value={stack3}
              onChange={(v) => setStack3(v as Stack3Filter)}
              options={STACK3_OPTIONS}
              title="Keep only stocks whose three moving averages are lined up one way on the last closed candle"
              className="w-64"
            />
            <NumberFilter
              label="Both gaps widening for at least (0 = any)"
              value={minWiden}
              onChange={(v) => setMinWiden(Math.min(30, Math.max(0, Math.round(v))))}
              min={0}
              max={10}
              step={1}
              suffix=" candles"
              className="w-56"
            />
          </FilterGroup>
        ) : null}
        {!saved && trendAlert && (
          <FilterGroup title="Trend rank alert">
            <FilterField label="Alert">
              <Toggle
                label={trendAlert.on ? "On" : "Off"}
                checked={trendAlert.on}
                onChange={(on) => changeTrendAlert(on ? { on, ...alertFromFilters() } : { on })}
                title="After each Trend quality scan in market hours (yours or the server's auto re-scan), send the top ranks of the Trend on filter, each stock once a day. Information only: nothing is traded."
              />
              <Toggle label="Telegram" checked={trendAlert.telegram} onChange={(telegram) => changeTrendAlert({ telegram })} />
              <Toggle
                label="Phone call"
                checked={trendAlert.call}
                onChange={(call) => changeTrendAlert({ call })}
                title="Rings during market hours, at most once every 5 minutes (the call card in Settings)"
              />
            </FilterField>
            <NumberFilter
              label="Top ranks to alert"
              value={trendAlert.top_n}
              onChange={(v) => changeTrendAlert({ top_n: Math.min(20, Math.max(1, Math.round(v))) })}
              min={1}
              max={20}
              step={1}
              className="w-36"
            />
            <FilterField label="Uses">
              <span className="text-xs text-slate-400">
                {trendAlert.sizes.length
                  ? `${trendAlert.sizes.map((m) => `${m}m`).join(" / ")} · ${trendAlert.direction === "UP" ? "bullish" : trendAlert.direction === "DOWN" ? "bearish" : "same way"} · ${trendAlert.kind === "STRONG" ? "strong trend" : "SMA side"} · age ≤ ${trendAlert.max_age_min || "any"}${trendAlert.max_age_min ? " min" : ""} · gap ≥ ${trendAlert.min_gap_pct}%${trendAlert.skip_fading ? " · not fading" : ""}`
                  : "No candle size yet: tick Trend on sizes, then Use these filters"}
              </span>
              {!alertMatchesFilters && trendOn.length > 0 && (
                <button
                  type="button"
                  onClick={() => changeTrendAlert(alertFromFilters())}
                  className="min-h-9 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/50 hover:bg-accentSky/10"
                >
                  Use these filters
                </button>
              )}
            </FilterField>
            {trendAlert.recent.length > 0 && (
              <FilterField label="Last sent">
                <span className="text-xs text-slate-400">
                  {trendAlert.recent
                    .slice(0, 5)
                    .map((a) => `${clock(a.at)} #${a.rank} ${a.symbol} ${a.side === "BULLISH" ? "▲" : "▼"}`)
                    .join(" · ")}
                </span>
              </FilterField>
            )}
          </FilterGroup>
        )}
        <FilterGroup title={saved ? "Price" : "Price & alert"}>
          <NumberFilter
            label="Price from (0 = any)"
            value={priceMin}
            onChange={(v) => setPriceMin(Math.min(100000, Math.max(0, Math.round(v))))}
            min={0}
            max={5000}
            step={50}
            prefix="₹"
            className="w-36"
          />
          <NumberFilter
            label="Price to (0 = any)"
            value={priceMax}
            onChange={(v) => setPriceMax(Math.min(100000, Math.max(0, Math.round(v))))}
            min={0}
            max={5000}
            step={50}
            prefix="₹"
            className="w-36"
          />
          {!saved && (
          <NumberFilter
            label="Alert when quiet for ≥"
            value={quietMin}
            onChange={(v) => setQuietMin(Math.min(375, Math.max(1, Math.round(v))))}
            min={5}
            max={240}
            step={5}
            suffix="min"
            className="w-40"
          />
          )}
          {!saved && (
          <FilterField label="Quiet runner alert">
            <Toggle label="Telegram" checked={alertOnQuiet} onChange={setAlertOnQuiet} title="Ping the owner on Telegram for a new quiet runner" />
          </FilterField>
          )}
        </FilterGroup>
        {!saved && (
        <FilterGroup title="Scanning">
          <FilterField label="Stocks">
            <ScanListPicker value={lists} onChange={setLists} />
          </FilterField>
          <FilterField label="Server">
            <Toggle
              label="Auto re-scan"
              checked={auto?.on ?? false}
              onChange={(on) => changeAuto({ on })}
              title="The server scans by itself every N minutes while the market is open, even with this page closed"
            />
          </FilterField>
          <SelectFilter
            label="Re-scan every"
            value={auto?.every_min ?? 30}
            onChange={(v) => changeAuto({ every_min: Number(v) })}
            options={(auto?.every_choices ?? EVERY_CHOICES).map((m) => ({ value: m, label: `${m} min` }))}
            className="w-32"
          />
        </FilterGroup>
        )}
      </FilterRow>
      {auto && !saved ? <AutoLine auto={auto} paused={paused} /> : null}
      {toast && (
        <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          <span>🔔 Quiet runner — {toast}</span>
          <button type="button" onClick={() => setToast(null)} className="rounded px-2 py-0.5 text-amber-300 hover:bg-amber-500/20">
            Dismiss
          </button>
        </div>
      )}

      {error && <p className="mb-2 text-xs text-loss">{error}</p>}
      {data?.error && !error && (
        <p className="mb-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">{data.error}</p>
      )}
      {rows.length === 0 ? (
        <p className="py-4 text-center text-xs text-slate-500">
          {data?.running
            ? "Scanning…"
            : data?.error && (data.failed ?? 0) > 0 && (data.done ?? 0) <= (data.failed ?? 0)
              ? "The scan could not read any stock, so there is nothing to show yet."
              : data?.as_of && scannedThisCandle
                ? "No stock matches these filters. Change Show or clear the stock filter, or press Scan now after the next candle closes."
                : `${auto?.on ? `The server scans by itself every ${auto.every_min} min while the market is open, or press Scan now.` : "Nothing is scanned until you press Scan now."} A scan reads about 200 stocks one at a time and takes a few minutes${scannedThisCandle || !data?.as_of ? "" : `; the last scan was for the ${sizeWords(data.minutes)} candle (SMA ${data.sma_fast}/${data.sma_slow})`}.`}
        </p>
      ) : (
        <div className="overflow-x-auto" ref={paged.anchor}>
          {onArmMany ? <TickBar ticks={ticks} onArm={onArmMany} /> : null}
          <Pager p={paged} className="mb-2" />
          <ColumnHint moved={cols.moved} onReset={cols.reset} />
          <table className="w-full text-sm">
            <thead className="whitespace-nowrap text-xs uppercase text-slate-500 [&_th]:px-2">
              <tr>
                {onArmMany ? <TickHead ticks={ticks} symbols={rows.map((r) => r.symbol)} /> : null}
                {inOrder(cols.order, {
                  symbol: <SortTh label="Stock" k="symbol" sort={sort} onSort={onSort} text drag={cols.dragProps("symbol")} />,
                  ltp: <SortTh label="LTP" k="ltp" sort={sort} onSort={onSort} num title="Last traded price at the scan" drag={cols.dragProps("ltp")} />,
                  score: <SortTh label="Score" k="score" sort={sort} onSort={onSort} text drag={cols.dragProps("score")} />,
                  first: (
                    <SortTh
                      label="First seen"
                      k="first"
                      sort={sort}
                      onSort={onSort}
                      text
                      title="When the stock first appeared in this list today, with the filters set above (from today's saved scans on this candle size)"
                      drag={cols.dragProps("first")}
                    />
                  ),
                  rank: (
                    <SortTh
                      label="Rank"
                      k="rank"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="1 = the freshest trend with the widest SMA gap. The average of the stock's place by trend age (youngest first) and by SMA gap (widest first). Read on the Trend on sizes, or on this scan's candle when none is ticked"
                      drag={cols.dragProps("rank")}
                    />
                  ),
                  age: (
                    <SortTh
                      label="Trend age"
                      k="age"
                      sort={sort}
                      onSort={onSort}
                      num
                      title={haRead ? "Market minutes the current colour run has lasted (closed candles x size), and since when. On the Trend on sizes (the shortest), or on this scan's candle when none is ticked" : "Market minutes the SMAs have pointed this way (closed candles x size), and since when. On the Trend on sizes (the shortest), or on this scan's candle when none is ticked"}
                      drag={cols.dragProps("age")}
                    />
                  ),
                  gap: (
                    <SortTh
                      label={`${rowWords.gap} %`}
                      k="gap"
                      sort={sort}
                      onSort={onSort}
                      num
                      title={haRead ? "The colour run's move %, on the Trend on sizes (averaged) or on this scan's candle. ⚠ fading = on a size the run has given back more than half its widest move" : "|SMA fast − slow| / slow %, on the Trend on sizes (averaged) or on this scan's candle. ⚠ fading = on a size the gap is under half its widest in this trend"}
                      drag={cols.dragProps("gap")}
                    />
                  ),
                  vsIndex: (
                    <SortTh
                      label="vs Nifty"
                      k="vsIndex"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="The stock's move today minus Nifty's move today, in % points. Above 0 = stronger than the market, below 0 = weaker"
                      drag={cols.dragProps("vsIndex")}
                    />
                  ),
                  cpr: (
                    <SortTh
                      label="CPR"
                      k="cpr"
                      sort={sort}
                      onSort={onSort}
                      text
                      title={`Central Pivot Range from yesterday's high, low and close: its width in % of the pivot (${CPR_NARROW}% or less = narrow, a trending day is more likely; ${CPR_WIDE}% or more = wide, sideways), where the price is against it, and whether it is beyond yesterday's high or low`}
                      drag={cols.dragProps("cpr")}
                    />
                  ),
                  three: (
                    <SortTh
                      label="3 SMAs"
                      k="three"
                      sort={sort}
                      onSort={onSort}
                      text
                      title={thirdRead ? `SMA ${data?.sma_fast} / ${data?.sma_slow} / ${thirdRead}: stacked up or down, the two gaps in %, and for how many candles in a row both gaps have widened` : "Pick a Third SMA and scan to fill this column"}
                      drag={cols.dragProps("three")}
                    />
                  ),
                  trend: (
                    <SortTh label="Trend" k="trend" sort={sort} onSort={onSort} text title="Strong trend label and how many of the 7 checks pass" drag={cols.dragProps("trend")} />
                  ),
                  phase: (
                    <SortTh
                      label="Phase"
                      k="phase"
                      sort={sort}
                      onSort={onSort}
                      text
                      title="Today split into choppy / strong up / strong down stretches; the chip is the stretch the stock is in now"
                      drag={cols.dragProps("phase")}
                    />
                  ),
                  dayMove: <SortTh label="Day %" k="dayMove" sort={sort} onSort={onSort} num title="Last price vs today's first open" drag={cols.dragProps("dayMove")} />,
                  buyers: (
                    <SortTh
                      label="Buyers %"
                      k="buyers"
                      sort={sort}
                      onSort={onSort}
                      num
                      title={`Waiting buy orders as a share of all waiting orders (the rest are sellers), from the Most active scan${buyers.at ? ` of ${clock(buyers.at)} IST` : ""}. Empty until a Most active scan has read the stock`}
                      drag={cols.dragProps("buyers")}
                    />
                  ),
                  sizes: (
                    <th
                      {...cols.dragProps("sizes")}
                      className={clsx("px-2 pb-2 text-left font-medium", cols.dragProps("sizes").className)}
                      title={`Today's ${rowWords.crossesShort} on each candle size (— = not enough candles yet)`}
                    >
                      {haRead ? "Flips by size" : "Crosses by size"}
                    </th>
                  ),
                  crosses: (
                    <SortTh
                      label={rowWords.crosses}
                      k="crosses"
                      sort={sort}
                      onSort={onSort}
                      num
                      title={haRead ? `${rowWords.score}` : rowWords.score}
                      drag={cols.dragProps("crosses")}
                    />
                  ),
                  since: (
                    <SortTh label="Last cross (min)" k="since" sort={sort} onSort={onSort} num title="Minutes since the start of the last cross candle" drag={cols.dragProps("since")} />
                  ),
                  avgGap: (
                    <SortTh label="Avg gap (min)" k="avgGap" sort={sort} onSort={onSort} num title="Average time between today's crosses" drag={cols.dragProps("avgGap")} />
                  ),
                  avgMove: (
                    <SortTh
                      label="Avg move %"
                      k="avgMove"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="Average absolute close-to-close move between consecutive crosses"
                      drag={cols.dragProps("avgMove")}
                    />
                  ),
                  speed: (
                    <SortTh
                      label="Speed"
                      k="speed"
                      sort={sort}
                      onSort={onSort}
                      num
                      title="Signed % per minute over the last 10 minutes (same reader as the Movers page)"
                      drag={cols.dragProps("speed")}
                    />
                  ),
                  volume: <SortTh label="Volume" k="volume" sort={sort} onSort={onSort} num title="Shares traded today so far" drag={cols.dragProps("volume")} />,
                  arm: (
                    <th {...cols.dragProps("arm")} className={clsx("px-1 pb-2 text-right font-medium", cols.dragProps("arm").className)}>
                      Arm
                    </th>
                  ),
                })}
              </tr>
            </thead>
            <tbody>
              {paged.shown.map((r) => (
                <Row key={r.symbol} order={cols.order} row={r} buyer={saved ? null : buyers.by.get(r.symbol) ?? null} rank={ranks.get(r.symbol) ?? null} tick={onArmMany ? <TickCell ticks={ticks} symbol={r.symbol} /> : null} firstAt={firstSeen.get(r.symbol) ?? null} armedOn={armed.get(r.symbol.toUpperCase()) ?? []} arming={arming === r.symbol} onArm={() => onArm(r.symbol)} />
              ))}
            </tbody>
          </table>
          <Pager p={paged} className="mt-2" />
        </div>
      )}
    </section>
  );
}

function Row({
  order,
  row: r,
  rank,
  tick,
  firstAt,
  armedOn,
  arming,
  onArm,
  buyer,
}: {
  /** The stock's row in the latest Most active scan (buyers vs sellers waiting). */
  buyer: ActiveStock | null;
  order: readonly Col[];
  row: ChopScanRow;
  rank: RankedTrend | null;
  tick: React.ReactNode;
  firstAt: string | null;
  armedOn: string[];
  arming: boolean;
  onArm: () => void;
}) {
  return (
    <tr className="whitespace-nowrap border-t border-slate-800/70 [&>td]:px-2">
      {tick}
      {inOrder(order, {
        symbol: (
          <td className="py-1.5 font-semibold text-slate-100"><GrowwLink symbol={r.symbol} /></td>
        ),
        ltp: <td className="py-1.5 text-right tabular-nums">{num(r.ltp)}</td>,
        score: (
          <td className="py-1.5">
            <span
              className={clsx("rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset", SCORE_CLASS[r.score])}
              title={
                r.score === "TRENDING"
                  ? "Trending — few crosses, long run since the last one"
                  : r.score === "CHOPPY"
                    ? "Choppy — many crosses, short gaps between them"
                    : "Mixed — neither trending nor obviously choppy"
              }
            >
              {SCORE_LABEL[r.score]}
              {r.last_cross_direction === "BULLISH" ? " ↑" : r.last_cross_direction === "BEARISH" ? " ↓" : ""}
            </span>
            {r.quiet_runner && (
              <span
                className="ml-1 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-[11px] font-semibold text-amber-300 ring-1 ring-inset ring-amber-500/40"
                title={`Quiet runner — had a cross earlier today and has been silent since. Possible strong ${
                  r.last_cross_direction === "BULLISH" ? "bullish" : r.last_cross_direction === "BEARISH" ? "bearish" : "trend"
                }.`}
              >
                🔔 Quiet {r.minutes_since_last_cross == null ? "" : `${Math.max(0, Math.round(r.minutes_since_last_cross))}m`}
              </span>
            )}
          </td>
        ),
        first: (
          <td className="py-1.5 tabular-nums text-xs">
            {firstAt ? (
              <span
                className="text-slate-200"
                title={[
                  `First in this list today at ${clock(firstAt)} IST`,
                  r.first_quiet_at ? `Quiet runner since ${clock(r.first_quiet_at)} IST` : "",
                  r.alerted_at ? `Telegram sent ${clock(r.alerted_at)} IST` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              >
                {clock(firstAt)}
                {r.alerted_at ? " 📨" : ""}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        rank: (
          <td className="py-1.5 text-right tabular-nums">
            {rank ? (
              <span className={clsx("font-semibold", rank.rank <= 3 ? "text-amber-300" : "text-slate-200")} title={`Rank ${rank.rank} of ${rank.of}`}>
                #{rank.rank}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        age: (
          <td className="py-1.5 text-right tabular-nums text-xs">
            {rank ? (
              <span title={`All ticked sizes this way since ${sinceText(rank.sinceTs)} IST`}>
                <span className={rank.side === "BULLISH" ? "text-profit" : "text-loss"}>{rank.side === "BULLISH" ? "▲" : "▼"}</span>{" "}
                <span className="text-slate-200">{ageText(rank.ageMin)}</span>
                <span className="ml-1 text-slate-500">{sinceText(rank.sinceTs)}</span>
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        gap: (
          <td className="py-1.5 text-right tabular-nums text-xs">
            {rank ? (
              <span className={rank.fading ? "text-amber-300" : "text-slate-200"}>
                {rank.gapPct.toFixed(2)}%{rank.fading ? " ⚠ fading" : ""}
              </span>
            ) : (
              <span className="text-slate-600">—</span>
            )}
          </td>
        ),
        vsIndex: (
          <td className="py-1.5 text-right tabular-nums text-xs">
            {r.rs_pct == null ? (
              <span className="text-slate-600" title="Nifty could not be read in this scan">—</span>
            ) : (
              <span className={r.rs_pct >= 0 ? "text-profit" : "text-loss"} title={`Stock ${fmtGap(r.day_move_pct)} · Nifty ${fmtGap(r.index_move_pct)}`}>
                {fmtGap(r.rs_pct)}
              </span>
            )}
          </td>
        ),
        cpr: (
          <td className="py-1.5 text-xs">
            {r.cpr_width_pct == null ? (
              <span className="text-slate-600" title="Needs the previous session's candles">—</span>
            ) : (
              <span
                className="inline-flex flex-wrap items-center gap-1.5"
                title={`CPR ${r.cpr_low} to ${r.cpr_high} · yesterday's low ${r.prev_low}, high ${r.prev_high}`}
              >
                <span className="tabular-nums text-slate-200">{r.cpr_width_pct.toFixed(2)}%</span>
                {r.cpr_width_pct <= CPR_NARROW ? (
                  <span className="rounded bg-profit/15 px-1.5 py-0.5 font-semibold text-profit">narrow</span>
                ) : r.cpr_width_pct >= CPR_WIDE ? (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-semibold text-amber-300">wide</span>
                ) : null}
                <span className="text-slate-400">
                  {r.cpr_side === "ABOVE" ? "price above" : r.cpr_side === "BELOW" ? "price below" : "price inside"}
                  {r.prev_side === "ABOVE" ? " · over yest. high" : r.prev_side === "BELOW" ? " · under yest. low" : ""}
                </span>
              </span>
            )}
          </td>
        ),
        three: (
          <td className="py-1.5 text-xs">
            {!r.sma_third ? (
              <span className="text-slate-600" title="This scan had no third SMA">—</span>
            ) : r.gap_slow_third_pct == null ? (
              <span className="text-slate-500" title={`SMA ${r.sma_third} needs more candles than this stock has`}>not formed</span>
            ) : (
              <span
                className="inline-flex flex-wrap items-center gap-1.5"
                title={`Fast vs slow ${fmtGap(r.gap_fast_slow_pct)} · slow vs SMA ${r.sma_third} ${fmtGap(r.gap_slow_third_pct)}${r.stack3 ? ` · stacked for ${r.stack3_candles ?? 0} candle(s)` : " · lines not lined up one way"}`}
              >
                {r.stack3 ? (
                  <span className={clsx("rounded px-1.5 py-0.5 font-semibold", r.stack3 === "BULLISH" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                    {r.stack3 === "BULLISH" ? "Stacked ↑" : "Stacked ↓"}
                  </span>
                ) : (
                  <span className="text-slate-500">Mixed</span>
                )}
                <span className="tabular-nums text-slate-400">
                  {fmtGap(r.gap_fast_slow_pct)} · {fmtGap(r.gap_slow_third_pct)}
                </span>
                {r.stack3 ? (
                  (r.widen_candles ?? 0) > 0 ? (
                    <span className="text-slate-200">widening {r.widen_candles}</span>
                  ) : (
                    <span className="text-amber-300">not widening</span>
                  )
                ) : null}
              </span>
            )}
          </td>
        ),
        trend: (
          <td className="py-1.5">
            <span className="inline-flex flex-wrap items-center gap-1">
              <TrendChip row={r} />
              <FreshChip row={r} />
            </span>
          </td>
        ),
        phase: (
          <td className="py-1.5">
            <PhaseCell row={r} />
          </td>
        ),
        dayMove: (
          <td className="py-1.5 text-right tabular-nums">
            {r.day_move_pct == null ? (
              "—"
            ) : (
              <span className={r.day_move_pct >= 0 ? "text-profit" : "text-loss"}>
                {r.day_move_pct >= 0 ? "+" : ""}
                {r.day_move_pct.toFixed(2)}%
              </span>
            )}
          </td>
        ),
        buyers: (
          <td className="py-1.5">
            {buyer?.buy_share == null ? (
              <span className="text-slate-600" title="No Most active scan has read this stock yet">—</span>
            ) : (
              <span
                className="inline-flex items-center gap-1.5"
                title={`Buy ${buyer.buy_qty?.toLocaleString("en-IN") ?? "?"} · Sell ${buyer.sell_qty?.toLocaleString("en-IN") ?? "?"} waiting`}
              >
                <span className="flex h-1.5 w-12 overflow-hidden rounded bg-loss/60">
                  <span className="h-full bg-profit" style={{ width: `${Math.round(buyer.buy_share * 100)}%` }} />
                </span>
                <span className="text-xs tabular-nums text-slate-300">
                  {Math.round(buyer.buy_share * 100)}% / {100 - Math.round(buyer.buy_share * 100)}%
                </span>
              </span>
            )}
          </td>
        ),
        sizes: (
          <td className="py-1.5">
            <CrossSizes row={r} />
          </td>
        ),
        crosses: (
          <td className="py-1.5 text-right tabular-nums">{r.crosses_today}</td>
        ),
        since: (
          <td className="py-1.5 text-right tabular-nums">{r.minutes_since_last_cross == null ? "—" : Math.max(0, Math.round(r.minutes_since_last_cross))}</td>
        ),
        avgGap: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">{num(r.avg_minutes_between, 1)}</td>
        ),
        avgMove: (
          <td className="py-1.5 text-right tabular-nums text-slate-400">{r.avg_move_pct == null ? "—" : `${r.avg_move_pct.toFixed(2)}%`}</td>
        ),
        speed: (
          <td className="py-1.5 text-right tabular-nums">
            {r.speed_pct_per_min == null ? (
              <span className="text-slate-600" title="Not enough candles to say">—</span>
            ) : (
              <span
                className={r.speed_pct_per_min >= 0 ? "text-profit" : "text-loss"}
                title={`${r.move_pct != null ? `${r.move_pct >= 0 ? "+" : ""}${r.move_pct.toFixed(2)}% ` : ""}over the last ${r.window_min ?? 10} minutes`}
              >
                {r.speed_pct_per_min >= 0 ? "+" : ""}
                {r.speed_pct_per_min.toFixed(3)}
              </span>
            )}
          </td>
        ),
        volume: (
          <td className="py-1.5 text-right tabular-nums text-slate-300">{shares(r.volume)}</td>
        ),
        arm: (
          <td className="py-1.5 text-right">
            <span className="inline-flex items-center justify-end gap-1.5">
              {armedOn.length ? (
                <span className="text-[11px] font-semibold text-profit" title={`Armed on ${armedOn.join(", ")}`}>
                  {armedOn.join(" · ")}
                </span>
              ) : null}
              <button
                type="button"
                disabled={arming}
                onClick={onArm}
                aria-label={`Arm ${r.symbol} on a bot`}
                title="Arm on a bot (asks which)"
                className="min-h-9 rounded-md px-2.5 text-xs font-semibold text-accentSky ring-1 ring-inset ring-accentSky/40 hover:bg-accentSky/10 disabled:opacity-50"
              >
                {arming ? "…" : "Arm"}
              </button>
            </span>
          </td>
        ),
      })}
    </tr>
  );
}

const FRESH_TEXT: Record<string, string> = {
  cross: "The two averages crossed within the last 3 candles",
  vwap: "Price and the fast average are on the trend's side of VWAP",
  gap: "The gap between the averages is still widening",
  volume: "Volume today is at least 1.5× the usual for this time",
  colour: "The last candle is the trend's colour",
  turn: "The candle colour turned within the last 3 candles and held",
  nowick: "The last candle has no wick against the new colour",
};

/** "Fresh ↑ / ↓": a trend that has only just started (new cross + VWAP + widening gap + volume). */
function FreshChip({ row: r }: { row: ChopScanRow }) {
  if (!r.fresh) return null;
  const up = r.fresh === "BULLISH";
  const checks = r.fresh_checks ?? {};
  const title = [
    `Fresh ${up ? "up" : "down"} trend: it has only just started.`,
    ...Object.keys(FRESH_TEXT).filter((k) => k in checks).map((k) => `${checks[k] ? "✓" : "✗"} ${FRESH_TEXT[k]}`),
    r.fresh_rvol != null ? `Volume ${r.fresh_rvol.toFixed(1)}× the usual` : "",
    "In a 30-day test this was the best early signal, but the edge is small. Watch only.",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span
      className={clsx(
        "whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        up ? "bg-sky-500/10 text-sky-300 ring-sky-400/40" : "bg-amber-500/10 text-amber-300 ring-amber-400/40"
      )}
      title={title}
    >
      Fresh {up ? "↑" : "↓"}
    </span>
  );
}

function TrendChip({ row: r }: { row: ChopScanRow }) {
  const checks = r.trend_checks ?? {};
  const order = orderFor(r);
  const texts = textsFor(r);
  const passed = order.filter((k) => checks[k]).length;
  const ha = r.trend_mode === "HEIKIN_ASHI" || r.trend_mode === "COLOUR";
  const title = [
    ha
      ? `${r.trend_mode === "COLOUR" ? "Colour read of the real candles" : "Heikin Ashi read"}: colour runs, wicks and bodies of the candles (no SMA)`
      : r.trend_mode === "MORNING" ? "Morning read (before 10:00, today's candles)" : "Midday read (10:00 on)",
    r.trend ? `Strong ${r.trend === "STRONG_BULLISH" ? "bullish" : "bearish"} trend` : `Not a strong trend (${r.trend_side?.toLowerCase() ?? "no"} side)`,
    ...order.map((k) => `${checks[k] ? "✓" : "✗"} ${r.trend_notes?.[k] ?? texts[k] ?? k}`),
    r.trend_skip_min ? `First ${r.trend_skip_min} min of today left out${r.trend_notes?.skip ? `: ${r.trend_notes.skip}` : ""}` : "",
    r.vwap != null ? `VWAP ${r.vwap.toFixed(2)}` : "VWAP not available",
    r.sma_gap_pct != null ? `${ha ? "Run move" : "SMA gap"} ${r.sma_gap_pct >= 0 ? "+" : ""}${r.sma_gap_pct.toFixed(2)}%` : "",
  ]
    .filter(Boolean)
    .join("\n");
  if (!r.trend_side) return <span className="text-slate-600">—</span>;
  if (!r.trend)
    return (
      <span className="text-xs tabular-nums text-slate-500" title={title}>
        {r.trend_side === "BULLISH" ? "↑" : "↓"} {passed}/{order.length}
      </span>
    );
  const bull = r.trend === "STRONG_BULLISH";
  return (
    <span
      className={clsx(
        "rounded-md px-1.5 py-0.5 text-xs font-semibold ring-1 ring-inset",
        bull ? "bg-profit/10 text-profit ring-profit/40" : "bg-loss/10 text-loss ring-loss/40"
      )}
      title={title}
    >
      {bull ? "Strong ↑" : "Strong ↓"} {passed}/7
    </span>
  );
}

const istTime = (ts: number | null | undefined) =>
  ts == null ? "" : new Date(ts * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

/** Today's crosses on 1 / 5 / 15-minute candles (the other sizes on hover): 0 is green with the side;
 * one cross shows when it happened and which way. */
const PHASE_LOOK: Record<ChopPhaseSide, { label: string; chip: string; bar: string }> = {
  BULL: { label: "Strong up ↑", chip: "bg-profit/10 text-profit ring-profit/30", bar: "bg-profit" },
  BEAR: { label: "Strong down ↓", chip: "bg-loss/10 text-loss ring-loss/30", bar: "bg-loss" },
  CHOP: { label: "Choppy", chip: "bg-slate-500/10 text-slate-400 ring-slate-600/40", bar: "bg-slate-600/60" },
};
const SESSION_MIN = 375; // 09:15 to 15:30 IST
/** trend_phases.MIN_RUN: trending candles in a row before a strong phase is named. */
const PHASE_MIN_RUN = 5;

/** The phase now as a chip, and today's phases as a strip from 09:15 to 15:30. */
function PhaseCell({ row: r }: { row: ChopScanRow }) {
  const all = r.phases ?? [];
  if (!r.phase || !all.length) return <span className="text-slate-600">—</span>;
  const look = PHASE_LOOK[r.phase];
  const step = (r.minutes || 5) * 60;
  const dayStart = (() => {
    const first = all[0].start_ts;
    const ist = first + 19_800;
    return ist - (ist % 86_400) + 33_300 - 19_800; // 09:15 IST that day
  })();
  const pct = (ts: number) => Math.max(0, Math.min(100, ((ts - dayStart) / 60 / SESSION_MIN) * 100));
  const move = (m: number | null) => (m == null ? "" : ` ${m >= 0 ? "+" : ""}${m.toFixed(2)}%`);
  const tip = all.map((p) => `${PHASE_LOOK[p.side].label} ${istTime(p.start_ts)}–${istTime(p.end_ts + step)}${move(p.move_pct)}`).join("\n");
  if (r.phase_early) {
    // A strong phase needs 5 trending candles in a row; before 5 of today's candles have closed, "choppy" means nothing.
    const readyAt = dayStart + PHASE_MIN_RUN * step;
    return (
      <span
        className="whitespace-nowrap rounded px-1.5 py-0.5 text-xs text-slate-400 ring-1 ring-inset ring-slate-600/40"
        title={`Too early to tell: a strong phase needs ${PHASE_MIN_RUN} trending ${r.minutes}-min candles in a row, and fewer than ${PHASE_MIN_RUN} of today's have closed. Readable from about ${istTime(readyAt)}, or use smaller candles.`}
      >
        Not enough candles yet
      </span>
    );
  }
  return (
    <div className="min-w-[10rem]" title={`Today's phases (${r.minutes}-min candles):\n${tip}`}>
      <span className={clsx("whitespace-nowrap rounded px-1.5 py-0.5 text-xs ring-1 ring-inset", look.chip)}>
        {look.label} since {istTime(r.phase_since_ts)}
        {r.phase !== "CHOP" && <span className="tabular-nums">{move(r.phase_move_pct ?? null)}</span>}
      </span>
      <div className="relative mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800/60" aria-hidden>
        {all.map((p) => (
          <span
            key={p.start_ts}
            className={clsx("absolute inset-y-0", PHASE_LOOK[p.side].bar)}
            style={{ left: `${pct(p.start_ts)}%`, width: `${Math.max(0.8, pct(p.end_ts + step) - pct(p.start_ts))}%` }}
          />
        ))}
      </div>
    </div>
  );
}

function CrossSizes({ row: r }: { row: ChopScanRow }) {
  const by = r.crosses_by_minutes ?? {};
  const side = r.side_by_minutes ?? {};
  const lastTs = r.last_cross_ts_by_minutes ?? {};
  const lastSide = r.last_cross_side_by_minutes ?? {};
  const arrow = (s: string | null | undefined) => (s === "BULLISH" ? "↑" : s === "BEARISH" ? "↓" : "");
  const all = CANDLE_CHOICES.map((m) => {
    const k = String(m);
    const last = lastTs[k] != null ? ` last ${istTime(lastTs[k])} ${arrow(lastSide[k])}` : "";
    return `${sizeShort(m)}: ${by[k] ?? "—"}${last}`;
  });
  return (
    <span className="inline-flex gap-1 text-xs tabular-nums" title={`Today's SMA crosses — ${all.join(" · ")}`}>
      {([1, 5, 15] as const).map((m) => {
        const k = String(m);
        const n = by[k];
        return (
          <span
            key={m}
            className={clsx(
              "rounded px-1.5 py-0.5 ring-1 ring-inset",
              n === 0
                ? "bg-profit/10 text-profit ring-profit/30"
                : n === 1
                  ? "bg-accentSky/10 text-accentSky ring-accentSky/30"
                  : n == null
                    ? "text-slate-600 ring-slate-800"
                    : "text-slate-400 ring-slate-700"
            )}
          >
            {m}m {n ?? "—"}
            {n === 0 && side[k] ? ` ${arrow(side[k])}` : ""}
            {n === 1 && lastTs[k] != null ? ` @${istTime(lastTs[k])} ${arrow(lastSide[k])}` : ""}
          </span>
        );
      })}
    </span>
  );
}

/** "14:45 IST", or "09:15 IST on 14 Oct" when it is not today. */
function whenText(iso: string) {
  const at = new Date(iso);
  const time = at.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  const day = (d: Date) => d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  return day(at) === day(new Date())
    ? `${time} IST`
    : `${time} IST on ${at.toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" })}`;
}

/** One line on the server's own re-scan: on/off, how often, when next, or why it did not run. */
function AutoLine({ auto, paused }: { auto: ChopAuto; paused: boolean }) {
  if (!auto.on)
    return <p className="mb-3 text-xs text-slate-500">Auto re-scan is off. Scans run only when you press Scan now.</p>;
  const next = auto.next_at ? new Date(auto.next_at) : null;
  const soon = next != null && next.getTime() <= Date.now();
  return (
    <p className="mb-3 text-xs text-slate-400">
      <span className="font-semibold text-profit">Auto re-scan on</span> · every {auto.every_min} min on the server, even with this
      page closed{auto.armed_only ? " · armed stocks" : " · every F&O stock"}
      {paused
        ? " · paused at the top of the page"
        : next
          ? soon
            ? " · next scan within a minute"
            : ` · next ${whenText(auto.next_at as string)}`
          : ""}
      {auto.error ? <span className="text-loss"> · last try: {auto.error}</span> : null}
    </p>
  );
}
