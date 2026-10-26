"""The backtest server's own Groww login, for the Scalp scans only (scan_session.py).

On the backtest server the replays borrow the desk's Groww session (backtest_server.remote_token),
so a scan there must not: its downloads would count against the same Groww rate limit as the live
bots. The scans use this server's own login instead, the one saved on its own Settings → Broker keys
(the encrypted access token in its own trading.db, `groww_client._read_desk_token`). It never logs
in by itself, so it cannot end any other server's session.

Candles only: the scans call `get_historical_candle_data` through `candle_history.fetch_frame` or
`scan_worker.run`; there is no order path here. The token is never logged or sent over HTTP.
"""
from __future__ import annotations

NO_LOGIN = "Scans on the backtest server use its own Groww login. Log in to Groww on this server's Settings → Broker keys first."


class OwnSession:
    """The attributes the scans read from a broker: token, adopt_saved_session, _recover_from_auth_failure, _require_sdk."""

    no_login_text = NO_LOGIN

    def __init__(self) -> None:
        self.token = ""
        self._sdk = None
        self._rejected: set[str] = set()

    def adopt_saved_session(self, *, force: bool = False) -> None:
        from groww_client import _read_desk_token

        saved = _read_desk_token().strip()
        if saved and saved not in self._rejected and saved != self.token:
            self.token = saved
            self._sdk = None

    def _recover_from_auth_failure(self) -> bool:
        if self.token:
            self._rejected.add(self.token)
        self.token = ""
        self._sdk = None
        self.adopt_saved_session(force=True)
        return bool(self.token)

    def _require_sdk(self):
        self.adopt_saved_session()
        if self._sdk is not None:
            return self._sdk
        if not self.token:
            raise RuntimeError(NO_LOGIN)
        from growwapi import GrowwAPI

        # Same as GrowwClient._require_sdk: the SDK's changelog call has no HTTP timeout.
        GrowwAPI._get_changelog = lambda _self: {}
        self._sdk = GrowwAPI(self.token)
        return self._sdk


_own = OwnSession()


def own_session() -> OwnSession:
    return _own
