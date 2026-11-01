"""What the chat assistant needs to read for one question.

Every answer used to be given the whole snapshot: the strategy guide, every
bot's settings and cross timelines, a week of trades with their strategies,
the settings catalog, the ORB desk's trades and console log, and the tool
schemas. Most of that is irrelevant to any one question, and the model is
billed for all of it, so a question about today's trades cost as much as one
about a setting change.

`classify` reads the question (and the user's previous turn, so a follow-up
keeps its subject) and names the topics it is about, the bots and stocks it
names, and whether it asks for a change. `sma_facts`, `build_context` and
`answer` then include only the sections those topics need. "Read everything"
(`full=True`) keeps the old behaviour for a question the trimming gets wrong.

Pure text matching; nothing here reads the engine or places an order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TOPICS = ("today", "past", "settings", "howto", "change", "crosses", "backtests", "orb")

# Desk pages whose questions are usually about the ORB desk rather than the SMA terminal.
ORB_PAGES = ("/", "/trade", "/movers", "/reports", "/scanner", "/chart")

_RX: dict[str, re.Pattern[str]] = {
    "today": re.compile(
        r"\btoday\b|\bhow many trades\b|\bthis morning\b|\bright now\b|\bcurrent|\bopen position|\blast trade\b"
        r"|\bp&l\b|\bpnl\b|\bprofit|\bloss|\bcharges?\b|\bwin rate\b|\bnet\b|\bth(at|is|ose|ese) trades?\b"
        r"|\barithmetic\b|\bcost\b|\bheld\b|\bholding\b|\bbook\b|\bmade\b|\bearn|\bwhat (were|was) (it|they)\b"
    ),
    "past": re.compile(
        r"\byesterday\b|\blast (week|day|days|month|\d+ days?)\b|\bpast\b|\bprevious\b|\bdays? ago\b|\bearlier\b"
        r"|\bhistory\b|\bover the last\b|\bused on\b|\b(mon|tues|wednes|thurs|fri)day\b"
        r"|\b\d{1,2}[/-]\d{1,2}\b|\b\d{1,2}(st|nd|rd|th)? ?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
        r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w* \d{1,2}\b|\bwhen did (i|you|we) change\b"
    ),
    "settings": re.compile(
        r"\bstrateg|\bsetting|\bconfig|\bstop\b|\bstop[- ]loss|\btrail|\btarget|\bfilter|\bcheck\b|\bcandle"
        r"|\bexit\b|\bvwap\b|\brsi\b|\badx\b|\bbollinger|\bgap mode\b|\bsupertrend|\bema\b|\batr\b|\bpivot"
        r"|\bam i using\b|\bare we using\b|\bqty\b|\bquantity\b|\bcut-?off\b|\bsquare[- ]off\b|\bturned? (on|off)\b"
        r"|\bflip\b|\bjoin trend\b|\bturn entry\b|\bmax trades\b|\bloss limit\b|\bsize\b|\bwhich of these\b"
    ),
    "howto": re.compile(
        r"\bhow (do|to|does|should|can|would) i?\b|\bexplain\b|\bwhat (is|does|are) (a|an|the)?\b|\bmean\b"
        r"|\bwork(s)?\b|\bbest way\b|\bshould i\b|\brecommend|\bwould you change\b|\bwell\b|\bguide\b|\badvice\b"
    ),
    "change": re.compile(
        r"\bturn (on|off)\b|\benable|\bdisable|\bswitch (on|off|to)\b|\bset\b|\buse\b|\barm\b|\bunarm\b|\badd\b"
        r"|\bremove\b|\bchange\b|\bmake it\b|\bput\b|\bapply\b|\bupdate\b|\bi want\b|\bno trades after\b|\bmax \d"
        r"|\bpick\b|\bchoose\b|\bminute candles\b|\bstocks? on bot\b|\bto bot\b"
    ),
    "crosses": re.compile(
        r"\bwhy\b|\bcross|\brevers|\bnot (start|take|enter|trade|trading|buy|buying|sell|selling|open)"
        r"|\bdidn'?t\b|\bdid not\b|\bisn'?t\b|\bis not\b|\brefus|\bignored?\b|\bsignal\b|\bskip|\bmiss(ed)?\b"
        r"|\bat \d{1,2}([:.]\d{2})?\s*(am|pm|o'?clock)?\b|\b\d{1,2}[:.]\d{2}\b|\bbuying anything\b|\bno trade\b"
        r"|\bwhat would have\b|\bwhat happened\b|\bwaiting\b|\bstuck\b"
    ),
    "backtests": re.compile(r"\bbacktest|\breplay|\brun\b|\bruns\b"),
    "orb": re.compile(
        r"\borb\b|\bopening[- ]range\b|\bmanual desk\b|\brange\b|\brvol\b|\bbreakout\b|\block(ed)?\b|\bkill[- ]switch\b"
        r"|\brisk engine\b|\bconsole\b|\blog\b|\bslippage\b|\bvirtual\b|\bstrategy account\b|\bdashboard\b"
    ),
}

# A question that starts like this asks about something; "use" / "set" / "add" in it are not an order to change.
_QUESTION_START = re.compile(r"^\s*(how|what|why|which|when|where|who|explain|did|does|do|is|are|was|were|can you (tell|explain|show)|tell me|show( me)?)\b")
# Change words that are an order even in a question ("can you turn on VWAP?").
_CHANGE_STRONG = re.compile(r"\bturn (on|off)\b|\benable|\bdisable|\bswitch (on|off|to)\b|\barm\b|\bunarm\b|\bchange .* to\b|\bset .* to\b|\bmake it\b|\bi want\b")
_BOT = re.compile(r"\bbot\s*#?\s*([1-5])\b")
_ALL_BOTS = re.compile(r"\b(all|every|each|other|which) (the |of the )?bots?\b|\bbots\b|\ball desks\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z&-]{1,15}")


@dataclass
class Scope:
    """What one question needs. `full` = the old everything snapshot."""

    full: bool = False
    topics: set[str] = field(default_factory=set)
    # Bots the question names ("1".."5", "research"); empty = work it out from the stocks named, else the page's bot.
    bots: set[str] = field(default_factory=set)
    all_bots: bool = False
    # Upper-cased words of the question that may be stock names; `sma_facts` keeps the ones it knows.
    words: set[str] = field(default_factory=set)
    page_bot: str | None = None
    orb: bool = False
    tools: bool = False
    fallback: bool = False

    def wants(self, *topics: str) -> bool:
        return self.full or any(t in self.topics for t in topics)

    def describe(self) -> dict:
        return {
            "full": self.full,
            "topics": sorted(self.topics),
            "bots": sorted(self.bots) if self.bots else (["all"] if self.all_bots else []),
            "page_bot": self.page_bot,
            "orb": self.orb,
            "tools": self.tools,
        }


def page_bot(page: str | None, screen: dict | None) -> str | None:
    """The bot of the page the question was asked from, when the page says."""
    data = screen if isinstance(screen, dict) else {}
    bot = data.get("bot")
    if isinstance(bot, (int, str)) and str(bot) in ("1", "2", "3", "4", "5", "research"):
        return str(bot)
    view = str(data.get("view") or "")
    if "research" in view.lower():
        return "research"
    if page and page.startswith("/terminal"):
        return "1"
    return None


def classify(message: str, page: str | None = None, screen: dict | None = None, full: bool = False, history: list[dict] | None = None) -> Scope:
    scope = Scope(full=bool(full), page_bot=page_bot(page, screen))
    text = (message or "").strip()
    # A follow-up ("show the arithmetic for that trade") keeps the subject of the question before it.
    previous = ""
    for turn in reversed(history or []):
        if (turn.get("role") or "") == "user" and (turn.get("content") or "").strip():
            previous = turn["content"].strip()
            break
    combined = f"{previous}\n{text}".lower() if previous else text.lower()
    low = text.lower()

    for name, rx in _RX.items():
        if rx.search(combined):
            scope.topics.add(name)
    if "change" in scope.topics and _QUESTION_START.match(low) and not _CHANGE_STRONG.search(low):
        scope.topics.discard("change")
    scope.tools = scope.full or "change" in scope.topics
    if "change" in scope.topics:
        # The current values are what the card changes from.
        scope.topics.add("settings")

    scope.bots = set(_BOT.findall(combined))
    if re.search(r"\bresearch\b", combined):
        scope.bots.add("research")
    if re.search(r"\bmain desk\b", combined):
        scope.bots.add("1")
    scope.all_bots = bool(_ALL_BOTS.search(combined)) and not scope.bots
    # Stock names from this turn and the one before it (a follow-up rarely repeats the stock).
    scope.words = {w.upper() for w in _WORD.findall(f"{previous} {text}")}

    on_desk_page = bool(page) and (page.rstrip("/") or "/") in ORB_PAGES
    about_sma = bool(scope.bots) or bool(re.search(r"\bsma\b|\bterminal\b|\bbot\b", combined))
    scope.orb = scope.full or "orb" in scope.topics or (on_desk_page and not about_sma)

    if not scope.topics:
        # Nothing recognisable: today's trades and the crosses of the page's bot, which answer most questions.
        scope.fallback = True
        scope.topics = {"today", "crosses"}
    return scope
