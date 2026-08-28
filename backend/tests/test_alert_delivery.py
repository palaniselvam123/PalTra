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
    DeliveryResult, classify_response, mask_phone, mask_secret, sanitize,
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
