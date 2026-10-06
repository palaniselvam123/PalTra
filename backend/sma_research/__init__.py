"""Offline research for the SMA 9/21 terminal: which stocks suit the strategy.

Read-only with respect to trading. It downloads past Groww 1-minute candles,
replays the unchanged SMA 9/21 strategy on them through the same
``ReplayEngine`` the Backtests tab uses (local fills, no order path), writes
its trades to its OWN SQLite file, and reports which stock-selection filters
would have helped. It never touches the live engine, its database, or any
order API.

Run it on the Fly machine, after market hours::

    python -m sma_research --out /data/research/sma_selection

See ``python -m sma_research --help``.
"""
