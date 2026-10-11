"""Changes the chat assistant may propose. It never applies them.

The model can only describe a change through one of these tools. The server
checks every proposal here and hands it to the page as a card; nothing is
saved until the person taps Apply, and the page then saves it through the same
terminal routes the Settings page uses. There is no tool for orders, closing a
position, the LIVE switch or the kill switch, so a reply cannot reach them.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

MAX_SYMBOLS = 20
_SYMBOL = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _symbols(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        name = str(raw).strip().upper()
        if not _SYMBOL.match(name):
            raise ValueError(f"'{raw}' is not an NSE symbol")
        if name not in out:
            out.append(name)
    return out


class TradeListProposal(BaseModel):
    bot: Literal[1, 2, 3, 4] = 1
    arm: list[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    unarm: list[str] = Field(default_factory=list, max_length=MAX_SYMBOLS)
    reason: str = Field(default="", max_length=500)

    @field_validator("arm", "unarm", mode="before")
    @classmethod
    def _clean(cls, value):
        return _symbols(value)


# Same bounds as the terminal's ConfigUpdate, so a card never offers a value the save would refuse.
class StopProposal(BaseModel):
    bot: Literal[1, 2, 3, 4] = 1
    symbol: str | None = None
    use_stop: bool | None = None
    stop_type: Literal["ATR", "SMA_GAP", "TSL"] | None = None
    atr_multiplier: float | None = Field(default=None, gt=0, le=10)
    tsl_mode: Literal["POINTS", "PERCENT"] | None = None
    tsl_sl_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_trail_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_target_points: float | None = Field(default=None, ge=0, le=100000)
    tsl_sl_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_trail_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_target_pct: float | None = Field(default=None, ge=0, le=100)
    gap_sl_mult: float | None = Field(default=None, gt=0, le=10)
    gap_tp_mult: float | None = Field(default=None, gt=0, le=20)
    gap_min_pct: float | None = Field(default=None, ge=0.01, le=5)
    reason: str = Field(default="", max_length=500)

    @field_validator("symbol", mode="before")
    @classmethod
    def _clean_symbol(cls, value):
        if value in (None, "", "ALL", "all"):
            return None
        return _symbols([value])[0]


STOP_FIELDS = [name for name in StopProposal.model_fields if name not in ("bot", "symbol", "reason")]

_BOT = {"type": "integer", "enum": [1, 2, 3, 4], "description": "Which SMA bot: 1 = main desk, 2-4 = bots 2-4."}
_REASON = {"type": "string", "description": "One short line on why, quoting the facts used."}

TOOLS = [
    {
        "type": "function",
        "name": "propose_trade_list_change",
        "description": (
            "Propose arming or unarming stocks on an SMA bot's Trade list. The user sees a card and must tap "
            "Apply; nothing changes before that. Arming a stock lets the bot trade it; it does not place an order."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bot": _BOT,
                "arm": {"type": "array", "items": {"type": "string"}, "description": "NSE symbols to arm."},
                "unarm": {"type": "array", "items": {"type": "string"}, "description": "NSE symbols to unarm."},
                "reason": _REASON,
            },
            "required": ["bot"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "propose_stop_change",
        "description": (
            "Propose stop-loss settings for an SMA bot, for all stocks (symbol omitted) or one stock. Only include "
            "the fields that change. The user sees old -> new values and must tap Apply. ATR: stop at "
            "atr_multiplier x ATR. TSL: trailing stop; tsl_mode POINTS uses tsl_sl_points / tsl_trail_points / "
            "tsl_target_points in rupees, PERCENT uses tsl_sl_pct / tsl_trail_pct / tsl_target_pct (% of entry); "
            "target 0 = none. SMA_GAP: gap_sl_mult / gap_tp_mult / gap_min_pct, PAPER only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "bot": _BOT,
                "symbol": {"type": "string", "description": "One stock, or omit for all stocks."},
                "use_stop": {"type": "boolean"},
                "stop_type": {"type": "string", "enum": ["ATR", "SMA_GAP", "TSL"]},
                "atr_multiplier": {"type": "number"},
                "tsl_mode": {"type": "string", "enum": ["POINTS", "PERCENT"]},
                "tsl_sl_points": {"type": "number"},
                "tsl_trail_points": {"type": "number"},
                "tsl_target_points": {"type": "number"},
                "tsl_sl_pct": {"type": "number"},
                "tsl_trail_pct": {"type": "number"},
                "tsl_target_pct": {"type": "number"},
                "gap_sl_mult": {"type": "number"},
                "gap_tp_mult": {"type": "number"},
                "gap_min_pct": {"type": "number"},
                "reason": _REASON,
            },
            "required": ["bot"],
            "additionalProperties": False,
        },
    },
]


def parse_proposal(name: str, arguments: str) -> tuple[dict | None, str | None]:
    """A checked proposal for the page, or why it was dropped."""
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments are not an object")
        if name == "propose_trade_list_change":
            p = TradeListProposal.model_validate(args)
            if not p.arm and not p.unarm:
                return None, "The Trade list change named no stocks."
            if set(p.arm) & set(p.unarm):
                return None, "The same stock was both armed and unarmed."
            return {"kind": "trade_list", "bot": p.bot, "arm": p.arm, "unarm": p.unarm, "reason": p.reason}, None
        if name == "propose_stop_change":
            p = StopProposal.model_validate(args)
            changes = {k: getattr(p, k) for k in STOP_FIELDS if getattr(p, k) is not None}
            if not changes:
                return None, "The stop change had no settings in it."
            return {"kind": "stop", "bot": p.bot, "symbol": p.symbol, "changes": changes, "reason": p.reason}, None
    except (ValueError, ValidationError) as exc:
        first = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        return None, f"A proposed change was not valid ({first})."
    return None, f"Unknown action '{name}'."


def proposals_from(data: dict) -> tuple[list[dict], list[str]]:
    """Every function call in a Responses API reply, checked."""
    found: list[dict] = []
    dropped: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        proposal, why = parse_proposal(str(item.get("name") or ""), str(item.get("arguments") or ""))
        if proposal is not None:
            found.append(proposal)
        elif why:
            dropped.append(why)
    return found, dropped
