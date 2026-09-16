"""Tests for the Firebase refresh-token exchange.

The property under test is a leak, not a behaviour: the Firebase Web API key is
passed to Google as a URL query parameter, and `requests` builds its HTTPError
message from the final URL. That message is logged by
`retry_with_exponential_backoff` and shipped to Sentry, so it must never carry
the key. `fileutils.scrub_event` can't help here — it redacts values under
sensitive *dict keys*, and this one is embedded in a message string.
"""

import pytest
import requests

from jobs.job import _SECURETOKEN_URL, _id_token_from_refresh

API_KEY = "AIzaSyTESTKEYDONOTUSE"


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.reason = "Bad Request"
        # What requests would have recorded: the fully-resolved URL, key included.
        self.url = f"{_SECURETOKEN_URL}?key={API_KEY}"
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} {self.reason} for url: {self.url}", response=self
            )

    def json(self):
        return self._payload


def test_returns_id_token_on_success(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **kw: _FakeResponse(200, {"id_token": "tok"})
    )
    assert _id_token_from_refresh("refresh", API_KEY) == "tok"


def test_api_key_is_sent_as_a_param_not_baked_into_the_url(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        return _FakeResponse(200, {"id_token": "tok"})

    monkeypatch.setattr(requests, "post", fake_post)
    _id_token_from_refresh("refresh", API_KEY)

    assert API_KEY not in seen["url"]
    assert seen["params"] == {"key": API_KEY}


def test_http_error_message_does_not_leak_the_api_key(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(400))

    with pytest.raises(requests.HTTPError) as excinfo:
        _id_token_from_refresh("refresh", API_KEY)

    assert API_KEY not in str(excinfo.value)


def test_no_chained_exception_smuggles_the_key(monkeypatch):
    """`raise ... from None` would not be enough.

    It sets __suppress_context__ (display only) while __context__ still points at
    the original exception, whose message holds the unredacted URL. Anything that
    walks the exception chain could surface it, so both links must be empty.
    """
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(400))

    with pytest.raises(requests.HTTPError) as excinfo:
        _id_token_from_refresh("refresh", API_KEY)

    err = excinfo.value
    assert err.__cause__ is None
    assert err.__context__ is None, "chained exception still carries the keyed URL"


def test_response_survives_for_the_retry_decorator(monkeypatch):
    """`retry_with_exponential_backoff` reads e.response.status_code to decide
    whether to retry, so redacting must not drop the response."""
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(400))

    with pytest.raises(requests.HTTPError) as excinfo:
        _id_token_from_refresh("refresh", API_KEY)

    assert excinfo.value.response is not None
    assert excinfo.value.response.status_code == 400
