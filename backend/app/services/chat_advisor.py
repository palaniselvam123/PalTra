"""Conversational explanation of the bot's own decisions.

The design constraint here is grounding. A chat assistant that answers "why did
you sell HDFCBANK?" from the model's imagination is worse than no assistant at
all: it produces confident, plausible reasons for trades that happened for
entirely different ones, and the user has no way to tell the difference.

So the model is never asked to recall or infer. Every answer is drawn from a
factual snapshot assembled here — the actual trade rows with their derived
costs and R-multiples, the opening ranges the bot measured, the risk state and
its lock reason, and the bot's own console log lines from decision time. The
engine's mechanics (slippage rate, charge formula, position-sizing rule) are
included too, because most "why didn't I make money" questions are answered by
arithmetic the user cannot see: a target narrower than the slippage, or a
position whose costs dwarf its edge.

The prompt forbids inventing anything outside that snapshot and requires the
model to say what it does not have recorded.
"""
from __future__ import annotations

import datetime as dt
import json

from sqlalchemy import select

from app import state
from app.core.market_clock import IST, ist_now, session_state
from app.models.database import AiAnalysis, StrategyLog, Trade, async_session
from app.services.ai_advisor import AiUnavailable, ai_advisor, supports_reasoning
from app.services.market_data import market_data
from app.services.paper_engine import (
    BROKERAGE_PCT,
    EXCHANGE_TXN_PCT,
    GST_PCT,
    SEBI_PCT,
    STAMP_DUTY_PCT,
    STT_PCT,
)
from app.services.reports import build_row
from app.services.strategy_runner import strategy_runner
from app.services.trade_ledger import get_account_summary

MAX_TRADES_IN_CONTEXT = 20
MAX_LOGS_IN_CONTEXT = 60
MAX_HISTORY_TURNS = 12

SYSTEM_PROMPT = """You are the ORB intraday trading bot, explaining your own decisions to the person running you.

GROUNDING — this is the rule that matters most:
Everything you know is in the FACTS JSON provided with the user's question. Never invent a trade, a price, a \
reason, a news event or a number that is not in it. If the user asks about something the facts do not cover, \
say plainly what you do not have recorded rather than guessing. You have no market data beyond what is in the \
facts and no memory of anything outside them.

PRICE LOOKUPS:
If `price_lookup` is present in the facts, the user asked what a stock traded at at a particular time and it has been looked up for you in the recorded minute prices. When `answered` is true, give the price and say which minute it came from — if `recorded_time_ist` differs from `requested_time_ist`, state that plainly rather than implying the figure is from the exact minute asked for. When `answered` is false, relay the `reason` as it stands; do not substitute a current price or estimate one.

HOW TO ANSWER:
- Quote the actual figures from the facts. When explaining a P&L outcome, show the arithmetic step by step so \
the user can check it: gross move, then charges, then the net.
- Prefer the trade's own recorded `exit_reason` over speculation about why a position closed.
- The `mechanics` section explains HOW your engine computes fills, costs and position size. Most "why didn't I \
make a profit" questions are answered there: a target narrower than the slippage charged on exit, or a \
position so large that charges dwarf the price move. Use it.
- Be blunt about your own bad trades. Do not spin a loss as a learning experience or pad it with encouragement. \
If costs, slippage or oversized position sizing caused a loss, say exactly that with the numbers.
- If a trade was structurally doomed — for example the target was closer than the round-trip costs — say so \
directly, and say which setting caused it.
- Keep it short. Two or three short paragraphs, or a tight list. Plain language, no jargon the user has not \
already seen in the app.

SCOPE:
This is a paper-trading sandbox using virtual money; no real orders are ever placed. You explain what you did \
and why. You do not give investment advice and you do not recommend what to buy or sell next. If asked to \
predict a price, decline and explain what you can actually tell them instead."""


def _mechanics() -> dict:
    """The engine constants behind every fill. Without these the model cannot
    explain a target-hit that still lost money.
    """
    return {
        "execution": "Paper only. Orders never reach the broker. Fills are simulated against the live tick.",
        "slippage_pct_per_leg": state.paper_engine.slippage_pct,
        "slippage_note": (
            f"Every fill is moved against the trader by {state.paper_engine.slippage_pct}% of price, on BOTH "
            "entry and exit. On a 700-rupee stock that is about 0.35 rupees per leg. If a target is closer to "
            "entry than the slippage, the trade books a loss even when the target is hit exactly."
        ),
        "charges_pct_of_turnover": {
            "brokerage": BROKERAGE_PCT,
            "stt_sell_side_only": STT_PCT,
            "exchange_txn": EXCHANGE_TXN_PCT,
            "gst_on_brokerage_and_exchange": GST_PCT,
            "sebi": SEBI_PCT,
            "stamp_duty_buy_side_only": STAMP_DUTY_PCT,
        },
        "charges_note": (
            "Charges are a percentage of turnover, capped so brokerage never exceeds Rs 20 per order — the "
            "same cap real discount brokers apply. Charges are applied on both legs; a large position still "
            "pays large STT/exchange/GST charges regardless of how far price actually moved, even though "
            "brokerage itself is capped."
        ),
        "position_sizing": (
            "Quantity = min(risk-based size, leverage-capped size). Risk-based size is (account capital x "
            "risk_per_trade_pct) / distance between entry and stop-loss — a very tight stop alone would ask "
            "for a very large position. That is bounded by max_leverage x account capital, so the smaller of "
            "the two wins; when the leverage cap binds, the trade carries LESS than the configured risk "
            "percentage, not more. Sizing uses the expected fill price (after slippage), not the raw signal "
            "price, since those differ and a tight stop makes the gap matter."
        ),
        "strategy": (
            "Opening Range Breakout. Measure the high and low over an opening window, then enter when a "
            "candle CLOSES outside that range: above the high goes long, below the low goes short. Stop-loss "
            "sits on the opposite side of the range, target at the configured reward-to-risk multiple. A "
            "Supertrend(10,3) trailing stop then ratchets the stop in the trade's favour and never loosens it."
        ),
        "adx_trend_filter": (
            "A breakout is only taken if ADX(14) — Wilder's Average Directional Index, a trend-strength "
            "measure that ignores direction — is at or above the configured threshold (default 20) at that "
            "moment. The reasoning: a range/close breaking the opening range means little if there is no "
            "underlying trend for price to follow through on; ADX below the threshold usually means a "
            "range-bound market where the 'breakout' is more likely noise. When ADX cannot yet be computed "
            "(not enough candle history early in a session), the filter does not block — it only blocks once "
            "it actually has a reading below threshold. This is separate from the risk gate below and runs "
            "before it, so a bot log line reading '[ADX FILTER] ... breakout ignored' means a real breakout "
            "signal fired but was rejected for lack of trend strength, before the risk manager was even "
            "consulted."
        ),
        "gates_every_entry_passes": (
            "Kill switch, existing-position check, live quote present, market open and feed not stale (live "
            "data only), stop-loss on the correct side of entry, ADX trend filter (bot entries only, if "
            "enabled), minimum-edge check (target must clear round-trip slippage and charges by a configured "
            "multiple), risk manager (position sizing, daily loss cap, daily trade cap, bid-ask spread guard), "
            "and the AI expert gate when enabled."
        ),
    }


async def build_context() -> dict:
    """Everything the assistant is allowed to know, as plain JSON."""
    snapshot = strategy_runner.snapshot()
    risk = state.risk_manager

    async with async_session() as session:
        trades = (
            await session.execute(select(Trade).order_by(Trade.id.desc()).limit(MAX_TRADES_IN_CONTEXT))
        ).scalars().all()
        logs = (
            await session.execute(select(StrategyLog).order_by(StrategyLog.id.desc()).limit(MAX_LOGS_IN_CONTEXT))
        ).scalars().all()
        analyses = (
            await session.execute(select(AiAnalysis).order_by(AiAnalysis.id.desc()).limit(10))
        ).scalars().all()

    account = await get_account_summary()
    health = market_data.health()
    now = ist_now()

    return {
        "now_ist": now.isoformat(),
        "market_session": session_state(now),
        "mode": state.mode,
        "kill_switch_active": state.kill_switch_active,
        "data_feed": {
            "source": health.source,
            "connected": health.connected,
            "stale": health.stale,
            "market_open": health.market_open,
            "note": (
                "SIMULATED means prices are synthetic and unrelated to real NSE quotes."
                if health.source == "simulated"
                else "LIVE means real NSE quotes via Groww."
            ),
        },
        "bot": {
            "enabled": snapshot.enabled,
            "status": snapshot.status,
            "status_meaning": {
                "STOPPED": "Off. No automated entries.",
                "WAITING_FOR_OPEN": "Waiting for the session open before measuring the range.",
                "BUILDING_RANGE": "Measuring the opening range. Cannot enter yet — there is no range to break.",
                "ARMED": "Range locked. Watching for breakout closes.",
                "NO_RANGE": "Range window closed with no usable candles, so no breakout can be detected and "
                "no entries will be taken this session.",
                "HALTED": "Stopped by the risk engine or the kill switch.",
            }.get(snapshot.status, ""),
            "config": snapshot.config,
            "late_start": snapshot.late_start,
            "late_start_note": (
                "The range was measured mid-session rather than from the 09:15 open, because the bot was "
                "started after the opening-range window had closed. Midday ranges are much narrower than "
                "the opening auction's, which produces much larger position sizes for the same rupee risk."
                if snapshot.late_start
                else None
            ),
            "range_window_ist": snapshot.range_window,
            "range_ready": snapshot.range_ready,
            "opening_ranges": snapshot.opening_ranges,
            "rvol_note": (
                "RVOL here is cross-sectional: a symbol's range volume divided by the median across the "
                "watchlist, because the feed has no 20-day history. Only symbols at or above the threshold "
                "are eligible to trade."
            ),
            "symbols_already_traded_today": snapshot.symbols_traded,
        },
        "risk": {
            "account_capital": risk.config.account_capital,
            "risk_per_trade_pct": risk.config.risk_per_trade_pct,
            "risk_per_trade_value": round(risk.config.account_capital * risk.config.risk_per_trade_pct / 100, 2),
            "daily_max_loss_pct": risk.config.daily_max_loss_pct,
            "daily_max_loss_value": round(risk.config.daily_max_loss_value, 2),
            "max_trades_per_day": risk.config.max_trades_per_day,
            "max_spread_pct": risk.config.max_spread_pct,
            "square_off_time_ist": risk.config.square_off_time_ist.strftime("%H:%M"),
            "state": {
                "trades_taken": risk.state.trades_taken,
                "realized_pnl": risk.state.realized_pnl,
                "locked": risk.state.locked,
                "lock_reason": risk.state.lock_reason,
                "lock_kind": risk.state.lock_kind,
            },
        },
        "account": account,
        "open_positions": [
            {
                **{k: v for k, v in pos.__dict__.items() if k != "opened_at"},
                "opened_at": pos.opened_at.replace(tzinfo=dt.timezone.utc).astimezone(IST).isoformat(),
                "ltp": (state.latest_quotes.get(pos.symbol) or {}).get("ltp"),
            }
            for pos in state.paper_engine.positions.values()
        ],
        "trades": [build_row(t) for t in trades],
        "trades_note": (
            "net_pnl is AFTER slippage and charges. gross_pnl is the raw price move. "
            "charges_drag_pct is charges as a percentage of the gross move — above 100 means costs exceeded "
            "the entire price move. charges_recorded=false means the trade predates per-leg cost tracking, "
            "so its cost breakdown is unavailable (its net P&L is still after costs)."
        ),
        "recent_ai_views": [
            {
                "symbol": a.symbol,
                "stance": a.stance,
                "conviction": a.conviction,
                "sentiment": a.sentiment_label,
                "thesis": a.thesis,
                "gate_passed": a.gate_passed,
                "gate_reason": a.gate_reason,
                "trade_id": a.trade_id,
                "at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in analyses
        ],
        "console_log_newest_first": [
            {
                "at": l.created_at.replace(tzinfo=dt.timezone.utc).astimezone(IST).strftime("%H:%M:%S")
                if l.created_at
                else None,
                "level": l.level,
                "message": l.message,
            }
            for l in logs
        ],
        "mechanics": _mechanics(),
    }


async def answer(message: str, history: list[dict] | None = None) -> dict:
    """Answers one question against the current factual snapshot."""
    cfg = await ai_advisor.config()
    key = await ai_advisor._api_key()  # raises AiUnavailable with a clear message

    context = await build_context()

    # A point-in-time price ("what was RELIANCE at 11:00") lives in the recorder's
    # table, not in the trade snapshot. Resolve it here so the model can answer
    # from a fact rather than being forced to say it has nothing recorded.
    from app.services.price_questions import lookup_async as price_lookup

    priced = await price_lookup(message)
    if priced is not None:
        context["price_lookup"] = priced

    conversation: list[dict] = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            conversation.append({"role": role, "content": content})

    conversation.append(
        {
            "role": "user",
            "content": f"FACTS (the only information you have):\n{json.dumps(context, default=str)}\n\n"
            f"Question: {message.strip()}",
        }
    )

    payload: dict = {
        "model": cfg.model,
        "instructions": SYSTEM_PROMPT,
        "input": conversation,
        # Generous because reasoning models bill their internal reasoning
        # against this cap before writing a word. It is a ceiling, not a spend.
        "max_output_tokens": 12000,
    }
    if supports_reasoning(cfg.model):
        # These are lookup-and-explain questions over facts already assembled
        # for the model, not problems needing deep deliberation.
        payload["reasoning"] = {"effort": "low"}

    import httpx

    async with httpx.AsyncClient(timeout=120.0) as client:
        res = await client.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )

    if res.status_code == 401:
        raise AiUnavailable("OpenAI rejected the key (401). Re-check it in Settings.", 401)
    if res.status_code == 429:
        raise AiUnavailable("OpenAI rate limit or quota exhausted (429).", 429)
    if res.status_code >= 400:
        raise AiUnavailable(f"OpenAI returned {res.status_code}: {res.text[:300]}", 502)

    data = res.json()
    if data.get("status") == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
        raise AiUnavailable(f"OpenAI returned an incomplete response ({reason}).", 502)

    text, _sources, _searched = ai_advisor._extract(data)
    if not text.strip():
        raise AiUnavailable("OpenAI returned an empty response.", 502)

    return {
        "reply": text.strip(),
        "model": cfg.model,
        "facts_summary": {
            "trades_in_context": len(context["trades"]),
            "log_lines_in_context": len(context["console_log_newest_first"]),
            "open_positions": len(context["open_positions"]),
            "bot_status": context["bot"]["status"],
        },
    }


def suggested_questions() -> list[str]:
    """Starter prompts, phrased the way the user actually asks."""
    return [
        "Why did you take the last trade?",
        "Why didn't that trade make a profit?",
        "Why is trading locked right now?",
        "Why isn't the bot buying anything?",
        "How is my position size decided?",
        "What did my charges cost me today?",
    ]
