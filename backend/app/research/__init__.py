"""Historical research data layer.

Deliberately separate from the live trading path. The live pipeline keeps an
in-memory rolling window sized for charting and scanning; research needs deep,
durable, reproducible history. Sharing one store would force each to accept
the other's constraints.

Nothing in this package is imported by the live tick loop, the scanner, or the
strategies.
"""
