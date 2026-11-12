"""Buyers / sellers test (pressure_study.py): signals from saved Most active scans, followed on real prices."""
import pressure_study


class FakeHistory:
    """Saved scans every 5 minutes from 10:00 to 15:20 on one day."""

    def __init__(self, book):
        self.book = book  # {(HH, MM): [rows]}
        self.metas = [{"id": i + 1, "as_of": f"2026-11-10T{h:02d}:{m:02d}:00+05:30"} for i, (h, m) in enumerate(sorted(book))]
        self.by_id = {meta["id"]: book[key] for meta, key in zip(self.metas, sorted(book))}

    def days(self, kind, limit=20):
        return [{"day": "2026-11-10"}]

    def runs(self, day, kind):
        return self.metas

    def get(self, run_id):
        return {"rows": self.by_id[run_id]}


def times():
    return [(h, m) for h in range(10, 16) for m in range(0, 60, 5) if (h, m) <= (15, 20)]


def build(price_of, share_of):
    return FakeHistory({t: [{"symbol": s, "ltp": price_of(s, t), "buy_share": share_of(s, t)} for s in ("UP", "DOWN", "FLAT")] for t in times()})


def minutes(t):
    return t[0] * 60 + t[1]


def test_buyers_signal_is_followed_on_later_prices():
    start = minutes((11, 0))

    def price(symbol, t):
        gone = max(0, minutes(t) - start)
        if symbol == "UP":
            return 100.0 + gone * 0.02      # rises 0.02 a minute after 11:00
        if symbol == "DOWN":
            return 200.0 - gone * 0.04
        return 50.0

    def share(symbol, t):
        if symbol == "UP":
            return 0.80 if minutes(t) >= start else 0.50
        if symbol == "DOWN":
            return 0.15 if minutes(t) >= start else 0.50
        return 0.50

    pressure_study._cache.clear()
    out = pressure_study.run(days=5, pct=75, gap_min=30, history=build(price, share))
    sig = {(s["symbol"], s["side"]): s for s in out["signals"]}
    assert set(sig) == {("UP", "BUY"), ("DOWN", "SELL")}          # one signal each: the stretch is not recounted
    up = sig[("UP", "BUY")]
    assert (up["time"], up["share_pct"], up["price"]) == ("11:00", 80.0, 100.0)
    assert up["15"] == 0.3 and up["30"] == 0.6 and up["60"] == 1.2  # +0.02 a minute on 100
    assert up["close"] == round((100.0 + 260 * 0.02 - 100.0), 3)    # 15:20 is 260 minutes later
    down = sig[("DOWN", "SELL")]
    assert down["30"] == 0.6 and down["worst"] > 0                  # it fell, so the sell signal was right
    buy = next(s for s in out["sides"] if s["side"] == "BUY")
    row = next(r for r in buy["after"] if r["after"] == "30")
    assert row["signals"] == 1 and row["right_pct"] == 100.0 and row["verdict"] == "few"
    assert row["base_right_pct"] is not None and row["edge"] > 0
    assert any("buyers reached 75%" in line for line in out["answer"])


def test_no_signal_below_the_threshold_and_first_scan_never_counts():
    pressure_study._cache.clear()
    out = pressure_study.run(days=5, pct=75, gap_min=30, history=build(lambda s, t: 100.0, lambda s, t: 0.74 if s == "UP" else 0.9))
    # UP never reaches 75 %; DOWN / FLAT sit at 90 % from the day's first scan, which starts no signal.
    assert out["total_signals"] == 0
    assert any("No scan had buyers" in line for line in out["answer"])
    row = next(r for r in out["by_threshold"] if r["pct"] == 70 and r["side"] == "BUY")
    assert row["signals"] == 0


def test_a_new_signal_only_after_the_gap():
    def share(symbol, t):
        if symbol != "UP":
            return 0.5
        return 0.8 if (minutes(t) // 10) % 2 else 0.5   # flips on and off every 10 minutes

    pressure_study._cache.clear()
    short = pressure_study.run(days=5, pct=75, gap_min=5, history=build(lambda s, t: 100.0, share))
    pressure_study._cache.clear()
    long = pressure_study.run(days=5, pct=75, gap_min=60, history=build(lambda s, t: 100.0, share))
    assert short["total_signals"] > long["total_signals"] > 0


def test_day_list_of_stocks_that_reached_and_whether_the_price_moved():
    start = minutes((11, 0))

    def price(symbol, t):
        gone = max(0, minutes(t) - start)
        if symbol == "UP":
            return 100.0 + gone * 0.02       # +0.6 % by 11:30, +1 % by 11:50
        if symbol == "DOWN":
            return 200.0 + gone * 0.01       # sellers were wrong: it rose
        return 50.0

    def share(symbol, t):
        if symbol == "UP":
            return 0.80 if start <= minutes(t) <= start + 20 else 0.50
        if symbol == "DOWN":
            return 0.15 if minutes(t) >= start else 0.50
        return 0.50

    out = pressure_study.reached(days=5, pct=75, move_pct=1.0, history=build(price, share))
    rows = {(r["symbol"], r["side"]): r for r in out["rows"]}
    assert set(rows) == {("UP", "BUY"), ("DOWN", "SELL")}
    up = rows[("UP", "BUY")]
    assert (up["first_at"], up["share_pct"], up["scans_at"], up["price"]) == ("11:00", 80.0, 5, 100.0)
    assert up["moved"] is True and up["moved_at"] == "11:50" and up["minutes_to_move"] == 50
    down = rows[("DOWN", "SELL")]
    assert down["moved"] is False and down["best_pct"] < 0
    all_, buy, sell = out["stats"]
    assert (all_["stocks"], all_["moved"], all_["correct_pct"]) == (2, 1, 50.0)
    assert buy["correct_pct"] == 100.0 and sell["correct_pct"] == 0.0
    assert any("right 50% of the time at a 1% move" in line for line in out["answer"])
    # A smaller move is reached sooner; one day can be picked.
    quick = pressure_study.reached(days=5, pct=75, move_pct=0.2, day="2026-11-10", history=build(price, share))
    assert next(r for r in quick["rows"] if r["symbol"] == "UP")["moved_at"] == "11:10"
    assert pressure_study.reached(days=5, pct=75, move_pct=0.2, day="2026-01-01", history=build(price, share))["total_rows"] == 0


def test_first_reached_uses_each_stock_read_time_and_the_read_before():
    """The scan's as_of is when the pass started; a row's read_at is when that stock's order book was read."""
    book = {
        (10, 0): [{"symbol": "UP", "ltp": 100.0, "buy_share": 0.5, "read_at": "2026-11-10T10:01:12+05:30"}],
        (10, 5): [{"symbol": "UP", "ltp": 101.0, "buy_share": 0.8, "read_at": "2026-11-10T10:07:41+05:30"}],
        (10, 10): [{"symbol": "UP", "ltp": 102.0, "buy_share": 0.8}],  # an older row: scan start only
    }
    out = pressure_study.reached(days=5, pct=75, move_pct=0.5, history=FakeHistory(book))
    up = out["rows"][0]
    assert (up["first_at"], up["below_at"]) == ("10:07:41", "10:01:12")
    assert up["moved_at"] == "10:10"
    first = pressure_study.reached(days=5, pct=75, history=FakeHistory({(10, 0): [{"symbol": "UP", "ltp": 1.0, "buy_share": 0.9}]}))
    assert first["rows"][0]["below_at"] is None
