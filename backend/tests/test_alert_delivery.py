"""Alert delivery classification, masking, and secret containment.

No real API key or real phone number appears anywhere in this file; every
credential is an obvious fixture value.

The central test is `test_the_exact_failure_that_was_reported_is_not_success`:
the provider answered HTTP 201 with an error body, the old code saw a 2xx and
reported delivery, and the user received nothing.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.alert_notifier import (
    ACCEPTED, CALLMEBOT, FAILED, MAX_ATTEMPTS, REJECTED, UNKNOWN, AlertNotifier,
    DeliveryResult, chat_id_from_updates, classify_response, mask_phone, mask_secret, sanitize,
)

FAKE_KEY = "TESTKEY123456"
FAKE_PHONE = "+910000000000"
REAL_FAILURE_BODY = "ERROR: apikey can not be <b>empty</b> or it has an <b>invalid format</b>"


class TestClassification:
    def test_the_exact_failure_that_was_reported_is_not_success(self):
        """HTTP 201 with an error body. The old rule (any 2xx) called this
        delivered, which is why a 'successful' send never arrived."""
        classification, _reason = classify_response(201, REAL_FAILURE_BODY)
        assert classification == REJECTED
        assert 200 <= 201 < 300, "the status really is a 2xx — that was the trap"

    def test_a_clean_acceptance_is_accepted_not_delivered(self):
        classification, reason = classify_response(200, "Message queued. You will receive it shortly")
        assert classification == ACCEPTED
        assert "delivery unconfirmed" in reason

    @pytest.mark.parametrize("body", [
        "ERROR: something went wrong",
        "APIKey is invalid",
        "The phone number is not registered",
        "You need to activate the API first",
        "Unauthorized",
    ])
    def test_provider_rejections_under_a_2xx_are_caught(self, body):
        assert classify_response(200, body)[0] == REJECTED

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_client_errors_fail(self, status):
        assert classify_response(status, "nope")[0] == FAILED

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_server_errors_fail(self, status):
        assert classify_response(status, "oops")[0] == FAILED

    def test_empty_body_is_acceptance_not_delivery(self):
        assert classify_response(200, "")[0] == ACCEPTED

    def test_unrecognisable_body_is_unknown_rather_than_assumed_good(self):
        assert classify_response(200, "zzzz")[0] == UNKNOWN

    def test_classification_is_deterministic(self):
        assert classify_response(201, REAL_FAILURE_BODY) == classify_response(201, REAL_FAILURE_BODY)


class TestMasking:
    def test_secret_is_described_never_revealed(self):
        out = mask_secret(FAKE_KEY)
        assert FAKE_KEY not in out
        assert str(len(FAKE_KEY)) in out

    def test_unset_secret(self):
        assert mask_secret("") == "<unset>"
        assert mask_secret(None) == "<unset>"

    def test_phone_keeps_only_country_code_and_last_two_digits(self):
        out = mask_phone(FAKE_PHONE)
        assert FAKE_PHONE not in out
        assert out.startswith("+91") and out.endswith("00")
        assert "*" in out

    def test_short_or_missing_phone(self):
        assert mask_phone("") == "<unset>"
        assert mask_phone("+91") == "<unset>"

    def test_sanitize_strips_the_key_from_a_url(self):
        url = f"https://api.example.com/send?phone={FAKE_PHONE}&apikey={FAKE_KEY}"
        out = sanitize(url, FAKE_KEY, FAKE_PHONE)
        assert FAKE_KEY not in out
        assert FAKE_PHONE not in out
        assert FAKE_PHONE.lstrip("+") not in out

    def test_sanitize_strips_a_url_encoded_phone(self):
        url = f"https://x/y?phone=%2B{FAKE_PHONE.lstrip('+')}&apikey={FAKE_KEY}"
        assert FAKE_PHONE.lstrip("+") not in sanitize(url, FAKE_KEY, FAKE_PHONE)

    def test_sanitize_strips_a_bare_number_even_without_a_parameter_name(self):
        assert "919876543210" not in sanitize("delivery failed for 919876543210")

    def test_sanitize_ignores_a_secret_that_is_only_a_plus(self):
        body = '{"ok":false,"description":"Bad Request: chat not found"}'
        assert sanitize(body, "+") == body

    def test_sanitize_handles_none(self):
        assert sanitize(None) == ""


class TestDeliveryPath:
    """The retry/classification path, driven with mocked transports.

    Coroutines are run with `asyncio.run` rather than pytest-asyncio, which is
    not a dependency of this project.
    """

    def _run(self, status: int, body: str, exc: Exception | None = None):
        n = AlertNotifier()
        calls = {"n": 0}

        async def fake_call():
            calls["n"] += 1
            if exc is not None:
                raise exc
            return httpx.Response(status, text=body, request=httpx.Request("GET", "https://x"))

        async def no_sleep(*_a, **_k):
            return None

        async def drive():
            original = asyncio.sleep
            asyncio.sleep = no_sleep          # keep retry tests instant
            try:
                return await n._with_retries(  # noqa: SLF001
                    fake_call, CALLMEBOT, FAKE_KEY, FAKE_PHONE
                )
            finally:
                asyncio.sleep = original

        return asyncio.run(drive()), calls["n"]

    def test_provider_rejection_is_reported_as_failure(self):
        result, calls = self._run(201, REAL_FAILURE_BODY)
        assert result.ok is False
        assert result.classification == REJECTED
        assert result.status_code == 201

    def test_a_rejection_is_not_retried(self):
        """A bad key stays bad; retrying risks a rate-limit ban on top."""
        _result, calls = self._run(201, REAL_FAILURE_BODY)
        assert calls == 1

    def test_acceptance_succeeds_but_is_not_marked_delivered(self):
        result, _ = self._run(200, "Message queued")
        assert result.ok is True
        assert result.classification == ACCEPTED
        assert result.delivery_confirmed is False

    def test_server_error_is_retried_then_fails(self):
        result, calls = self._run(503, "unavailable")
        assert result.ok is False
        assert calls == MAX_ATTEMPTS

    def test_timeout_is_retried_then_fails(self):
        result, calls = self._run(0, "", exc=httpx.TimeoutException("timed out"))
        assert result.ok is False
        assert calls == MAX_ATTEMPTS
        assert "network failure" in (result.error or "")

    def test_no_secret_reaches_the_result(self):
        result, _ = self._run(201, f"ERROR: apikey {FAKE_KEY} rejected for {FAKE_PHONE}")
        blob = f"{result.error} {result.provider_message}"
        assert FAKE_KEY not in blob
        assert FAKE_PHONE not in blob


class TestTelegram:
    def test_chat_id_comes_from_the_latest_message(self):
        payload = {
            "ok": True,
            "result": [
                {"update_id": 1, "message": {"chat": {"id": 111}, "text": "old"}},
                {"update_id": 2, "message": {"chat": {"id": 222}, "text": "hi"}},
            ],
        }
        assert chat_id_from_updates(payload) == "222"

    def test_empty_updates_have_no_chat(self):
        assert chat_id_from_updates({"ok": True, "result": []}) is None
        assert chat_id_from_updates({}) is None


class TestConfiguration:
    def test_missing_credentials_are_reported_not_attempted(self):
        r = DeliveryResult(False, CALLMEBOT, error="CallMeBot needs both a phone number and an API key")
        assert r.ok is False and r.delivery_confirmed is False

    def test_delivery_confirmed_requires_more_than_ok(self):
        assert DeliveryResult(True, CALLMEBOT, classification=ACCEPTED).delivery_confirmed is False
        assert DeliveryResult(True, CALLMEBOT, classification="DELIVERED").delivery_confirmed is True

    def test_no_real_credentials_appear_in_the_test_suite(self):
        """A test suite is a common place for a key to leak into version control.

        The forbidden patterns are assembled from fragments so this check does
        not match its own source — the first version failed on exactly that.
        """
        import pathlib

        needles = ["callmebot.com/whatsapp.php?" + "phone=+9", "api" + "key=9", "api" + "key=1234"]
        tests_dir = pathlib.Path(__file__).parent
        for path in tests_dir.glob("test_*.py"):
            src = path.read_text(encoding="utf-8")
            for needle in needles:
                assert needle not in src, f"{path.name} may contain a real credential"


class TestChannelRouting:
    """Trade alerts and critical alerts go to their own channels."""

    def _notifier(self, monkeypatch, enabled: dict[str, str]):
        notifier = AlertNotifier()
        delivered: list[tuple[str, str]] = []

        async def active_channel(channel_type: str = "trade"):
            provider = enabled.get(channel_type)
            return type("Row", (), {"provider": provider})() if provider else None

        async def deliver(channel, message):
            delivered.append((channel.provider, message))
            return DeliveryResult(True, channel.provider, classification=ACCEPTED)

        monkeypatch.setattr(notifier, "active_channel", active_channel)
        monkeypatch.setattr(notifier, "_deliver", deliver)
        return notifier, delivered

    def test_trade_alerts_never_reach_the_critical_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram", "critical": "twilio"})
        asyncio.run(notifier.send("fill"))
        assert delivered == [("telegram", "fill")]

    def test_critical_alerts_go_to_the_critical_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram", "critical": "twilio"})
        asyncio.run(notifier.send_critical("bot down"))
        assert delivered == [("twilio", "bot down")]

    def test_critical_falls_back_to_trade_when_no_critical_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram"})
        asyncio.run(notifier.send_critical("bot down"))
        assert delivered == [("telegram", "bot down")]

    def test_trade_alert_is_skipped_when_only_a_critical_channel_exists(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"critical": "twilio"})
        result = asyncio.run(notifier.send("fill"))
        assert delivered == [] and not result.ok


class TestQuietRunnerChannel:
    """The Scalp page's quiet runners go to their own Telegram bot when one is set."""

    def _notifier(self, monkeypatch, enabled: dict[str, str]):
        return TestChannelRouting._notifier(None, monkeypatch, enabled)

    def test_quiet_alerts_go_to_the_quiet_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram", "quiet": "telegram_quiet"})
        asyncio.run(AlertNotifier.send_quiet(notifier, "quiet runners"))
        assert delivered == [("telegram_quiet", "quiet runners")]

    def test_quiet_falls_back_to_trade_when_no_quiet_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram"})
        asyncio.run(AlertNotifier.send_quiet(notifier, "quiet runners"))
        assert delivered == [("telegram", "quiet runners")]

    def test_trade_alerts_never_reach_the_quiet_channel(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram", "quiet": "telegram_quiet"})
        asyncio.run(AlertNotifier.send(notifier, "fill"))
        asyncio.run(AlertNotifier.send_critical(notifier, "bot down"))
        assert delivered == [("telegram", "fill"), ("telegram", "bot down")]

    def test_the_quiet_bot_is_sent_through_telegram(self, monkeypatch):
        notifier = AlertNotifier()
        seen: list[tuple[str, str, str]] = []

        async def telegram(chat_id, token, message):
            seen.append((chat_id, token, message))
            return DeliveryResult(True, "telegram", classification=ACCEPTED)

        monkeypatch.setattr(notifier, "_send_telegram", telegram)
        monkeypatch.setattr(
            "app.services.alert_notifier.get_vault",
            lambda: type("Vault", (), {"decrypt": staticmethod(lambda v: v)})(),
        )
        row = type(
            "Row", (),
            {"provider": "telegram_quiet", "target_encrypted": "42", "secret_encrypted": "1:TEST", "extra_encrypted": ""},
        )()
        result = asyncio.run(notifier._deliver(row, "hi"))
        assert result.ok and seen == [("42", "1:TEST", "hi")]

    @pytest.mark.parametrize(
        "provider,channel_type",
        [("telegram_quiet", "trade"), ("telegram_quiet", "critical"), ("telegram", "quiet")],
    )
    def test_the_quiet_slot_only_takes_the_quiet_bot(self, provider, channel_type):
        from fastapi import HTTPException

        from app.api.routes_scanner_engine import ChannelRequest, save_channel

        body = ChannelRequest(provider=provider, channel_type=channel_type, secret="1:TEST", target="42")
        with pytest.raises(HTTPException) as err:
            asyncio.run(save_channel(body))
        assert err.value.status_code == 400


class TestOwnBotPerKind(TestChannelRouting):
    """News, signal (heads-ups, SMA 9 turns, reviews) and fast-move alerts each take their own Telegram
    bot, and critical alerts can have one too, so a second token never overwrites the trade bot."""

    ALL = {
        "trade": "telegram",
        "critical": "telegram_critical",
        "news": "telegram_news",
        "signal": "telegram_signal",
        "fast": "telegram_fast",
        "quiet": "telegram_quiet",
    }

    def test_each_kind_goes_to_its_own_bot(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, self.ALL)
        asyncio.run(AlertNotifier.send_news(notifier, "news"))
        asyncio.run(AlertNotifier.send_signal(notifier, "turn"))
        asyncio.run(AlertNotifier.send_fast(notifier, "fast"))
        asyncio.run(AlertNotifier.send_critical(notifier, "down"))
        asyncio.run(AlertNotifier.send(notifier, "fill"))
        assert delivered == [
            ("telegram_news", "news"),
            ("telegram_signal", "turn"),
            ("telegram_fast", "fast"),
            ("telegram_critical", "down"),
            ("telegram", "fill"),
        ]

    def test_a_kind_without_its_own_bot_falls_back_to_trade(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {"trade": "telegram"})
        asyncio.run(AlertNotifier.send_news(notifier, "news"))
        asyncio.run(AlertNotifier.send_signal(notifier, "turn"))
        asyncio.run(AlertNotifier.send_fast(notifier, "fast"))
        assert delivered == [("telegram", "news"), ("telegram", "turn"), ("telegram", "fast")]

    def test_trade_alerts_never_reach_the_other_bots(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, self.ALL)
        asyncio.run(AlertNotifier.send(notifier, "fill"))
        assert delivered == [("telegram", "fill")]

    def test_send_to_an_unknown_kind_with_nothing_set_is_skipped_not_raised(self, monkeypatch):
        notifier, delivered = self._notifier(monkeypatch, {})
        result = asyncio.run(AlertNotifier.send_to(notifier, "news", "news"))
        assert not result.ok and delivered == []

    @pytest.mark.parametrize("provider", ["telegram_critical", "telegram_news", "telegram_signal", "telegram_fast"])
    def test_each_own_bot_is_sent_through_telegram(self, monkeypatch, provider):
        notifier = AlertNotifier()
        seen: list[tuple[str, str, str]] = []

        async def telegram(chat_id, token, message):
            seen.append((chat_id, token, message))
            return DeliveryResult(True, provider, classification=ACCEPTED)

        class Vault:
            def decrypt(self, value):
                return value

        monkeypatch.setattr(notifier, "_send_telegram", telegram)
        monkeypatch.setattr("app.services.alert_notifier.get_vault", lambda: Vault())
        row = type("Row", (), {"provider": provider, "target_encrypted": "42", "secret_encrypted": "1:TEST", "extra_encrypted": ""})()
        result = asyncio.run(notifier._deliver(row, "hi"))
        assert result.ok and seen == [("42", "1:TEST", "hi")]

    @pytest.mark.parametrize(
        "provider,channel_type",
        [
            ("telegram_news", "trade"),
            ("telegram_signal", "news"),
            ("telegram_fast", "critical"),
            ("telegram_critical", "trade"),
            ("telegram", "news"),
            ("telegram", "signal"),
            ("twilio", "fast"),
        ],
    )
    def test_each_own_bot_stays_in_its_own_slot(self, provider, channel_type):
        from fastapi import HTTPException

        from app.api.routes_scanner_engine import ChannelRequest, save_channel

        body = ChannelRequest(provider=provider, channel_type=channel_type, secret="1:TEST", target="42", extra="x")
        with pytest.raises(HTTPException) as err:
            asyncio.run(save_channel(body))
        assert err.value.status_code == 400

    @pytest.mark.parametrize("kind", ["critical", "news", "signal", "fast", "quiet"])
    def test_a_test_message_never_falls_back_to_the_trade_bot(self, monkeypatch, kind):
        from fastapi import HTTPException

        from app.api.routes_scanner_engine import test_alert

        async def active_channel(channel_type="trade"):
            return type("Row", (), {"provider": "telegram"})() if channel_type == "trade" else None

        monkeypatch.setattr("app.api.routes_scanner_engine.alert_notifier.active_channel", active_channel)
        with pytest.raises(HTTPException) as err:
            asyncio.run(test_alert(channel_type=kind))
        assert err.value.status_code == 400 and kind in err.value.detail


class TestTwilioReplies:
    def test_a_queued_twilio_reply_is_accepted_despite_error_code_null(self):
        body = '{"sid": "CA123", "status": "queued", "error_code": null, "error_message": null}'
        assert classify_response(201, body)[0] == ACCEPTED

    def test_a_failed_twilio_reply_is_rejected(self):
        assert classify_response(201, '{"sid": "CA1", "status": "failed", "error_code": 21211}')[0] == REJECTED


class TestPhoneCalls:
    IST = __import__("datetime").timezone(__import__("datetime").timedelta(hours=5, minutes=30))

    def _notifier(self, monkeypatch, when, enabled=True):
        import datetime as dt

        from app.services import alert_notifier as mod

        notifier = AlertNotifier()
        rang: list[str] = []

        async def active_channel(channel_type: str = "trade"):
            if channel_type == "call" and enabled:
                return type("Row", (), {"provider": mod.TWILIO_VOICE})()
            return None

        async def deliver(channel, message):
            rang.append(message)
            return DeliveryResult(True, channel.provider, classification=ACCEPTED)

        monkeypatch.setattr(notifier, "active_channel", active_channel)
        monkeypatch.setattr(notifier, "_deliver", deliver)
        clock = {"now": when}
        monkeypatch.setattr(mod, "ist_now", lambda: clock["now"])
        return notifier, rang, clock, dt

    def test_rings_in_market_hours_once_per_cooldown(self, monkeypatch):
        import datetime as dt

        start = dt.datetime(2026, 10, 12, 11, 0, tzinfo=self.IST)  # Monday
        notifier, rang, clock, _ = self._notifier(monkeypatch, start)
        assert asyncio.run(notifier.place_call("bot 2 halted")).ok
        assert not asyncio.run(notifier.place_call("bot 3 halted")).ok
        clock["now"] = start + dt.timedelta(minutes=6)
        assert asyncio.run(notifier.place_call("bot 4 halted")).ok
        assert rang == ["bot 2 halted", "bot 4 halted"]

    def test_no_call_at_night_or_weekends_but_a_test_call_rings(self, monkeypatch):
        import datetime as dt

        notifier, rang, clock, _ = self._notifier(monkeypatch, dt.datetime(2026, 10, 12, 20, 0, tzinfo=self.IST))
        assert asyncio.run(notifier.place_call("x")).skipped_reason
        clock["now"] = dt.datetime(2026, 10, 11, 11, 0, tzinfo=self.IST)  # Sunday
        assert asyncio.run(notifier.place_call("x")).skipped_reason
        assert asyncio.run(notifier.place_call("test", test=True)).ok
        assert rang == ["test"]

    def test_the_spoken_text_is_escaped(self, monkeypatch):
        sent = {}

        class Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, auth, data):
                sent.update(data)
                return httpx.Response(201, text='{"sid": "CA1", "status": "queued", "error_code": null}')

        monkeypatch.setattr(httpx, "AsyncClient", Client)
        result = asyncio.run(AlertNotifier()._call_twilio(FAKE_PHONE, "AC1:tok", "+15550000000", "PalTra bot HALTED <b>&"))
        assert result.ok
        assert "&lt;b&gt;&amp;" in sent["Twiml"] and "<b>" not in sent["Twiml"]
        assert "Pal Tra bot HALTED" in sent["Twiml"]


def test_an_app_stop_rings_outside_market_hours_but_keeps_the_cooldown(monkeypatch):
    import datetime as dt

    night = dt.datetime(2026, 10, 11, 22, 0, tzinfo=TestPhoneCalls.IST)  # Sunday night
    notifier, rang, clock, _ = TestPhoneCalls()._notifier(monkeypatch, night)
    assert not asyncio.run(notifier.place_call("bot halted")).ok
    assert asyncio.run(notifier.place_call("app stopping", any_time=True)).ok
    assert not asyncio.run(notifier.place_call("app stopping again", any_time=True)).ok
    assert rang == ["app stopping"]


class TestAppStopAnnouncement:
    """The app stopping: always in market hours; after hours each part follows its switch."""

    def _notifier(self, monkeypatch, prefs):
        import datetime as dt

        notifier = AlertNotifier()
        calls: list[tuple[str, bool]] = []
        messages: list[tuple[str, bool]] = []

        async def stop_prefs():
            return prefs

        async def place_call(message, *, test=False, any_time=False):
            calls.append((message, any_time))
            return DeliveryResult(True, "twilio_voice", classification=ACCEPTED)

        async def send_critical(message, call=False, call_any_time=False):
            messages.append((message, call))
            if call:
                await place_call(message, any_time=call_any_time)
            return DeliveryResult(True, "telegram", classification=ACCEPTED)

        monkeypatch.setattr(notifier, "stop_prefs", stop_prefs)
        monkeypatch.setattr(notifier, "place_call", place_call)
        monkeypatch.setattr(notifier, "send_critical", send_critical)
        return notifier, calls, messages, dt

    def test_in_market_hours_it_always_messages_and_rings(self, monkeypatch):
        notifier, calls, messages, dt = self._notifier(
            monkeypatch, {"after_hours_stop_call": False, "after_hours_stop_message": False}
        )
        asyncio.run(notifier.announce_shutdown("stop", now=dt.datetime(2026, 10, 13, 11, 0)))
        assert messages == [("stop", True)] and len(calls) == 1

    def test_after_hours_both_switches_on_rings_at_any_hour_and_messages(self, monkeypatch):
        notifier, calls, messages, dt = self._notifier(
            monkeypatch, {"after_hours_stop_call": True, "after_hours_stop_message": True}
        )
        asyncio.run(notifier.announce_shutdown("stop", now=dt.datetime(2026, 10, 13, 20, 0)))
        assert calls == [("stop", True)] and messages == [("stop", False)]

    def test_after_hours_call_off_sends_only_the_message(self, monkeypatch):
        notifier, calls, messages, dt = self._notifier(
            monkeypatch, {"after_hours_stop_call": False, "after_hours_stop_message": True}
        )
        asyncio.run(notifier.announce_shutdown("stop", now=dt.datetime(2026, 10, 13, 20, 0)))
        assert calls == [] and messages == [("stop", False)]

    def test_after_hours_both_off_sends_nothing(self, monkeypatch):
        notifier, calls, messages, dt = self._notifier(
            monkeypatch, {"after_hours_stop_call": False, "after_hours_stop_message": False}
        )
        asyncio.run(notifier.announce_shutdown("stop", now=dt.datetime(2026, 10, 17, 10, 0)))  # a Saturday
        assert calls == [] and messages == []
