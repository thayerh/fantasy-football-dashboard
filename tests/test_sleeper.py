"""Sleeper client: retry policy and boundary conversions."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from app.sleeper import SleeperClient, SleeperError, ms_to_utc

BASE = "https://api.sleeper.app/v1"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries back off with time.sleep; tests shouldn't actually wait."""
    monkeypatch.setattr("app.sleeper.time.sleep", lambda _: None)


def test_ms_to_utc_returns_aware_utc():
    converted = ms_to_utc(1_757_874_600_000)
    assert converted.tzinfo is not None
    assert converted.utcoffset() == dt.timedelta(0)
    assert ms_to_utc(None) is None


@respx.mock
def test_get_league_returns_payload(settings, league_payload):
    route = respx.get(f"{BASE}/league/999999999999999999").mock(
        return_value=httpx.Response(200, json=league_payload)
    )
    with SleeperClient(settings) as client:
        assert client.get_league("999999999999999999")["name"] == league_payload["name"]
    assert route.called


@respx.mock
def test_retries_then_succeeds_on_transient_500(settings, league_payload):
    route = respx.get(f"{BASE}/league/999999999999999999").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=league_payload),
        ]
    )
    with SleeperClient(settings) as client:
        assert client.get_league("999999999999999999")["league_id"]
    assert route.call_count == 2


@respx.mock
def test_retries_are_exhausted_then_raise(settings):
    respx.get(f"{BASE}/league/999999999999999999").mock(return_value=httpx.Response(429))
    with SleeperClient(settings) as client, pytest.raises(SleeperError, match="after 3 attempts"):
        client.get_league("999999999999999999")


@respx.mock
def test_404_is_not_retried(settings):
    """A bad league id is permanent; retrying it just wastes the rate limit."""
    route = respx.get(f"{BASE}/league/nope").mock(return_value=httpx.Response(404))
    with SleeperClient(settings) as client, pytest.raises(SleeperError, match="404"):
        client.get_league("nope")
    assert route.call_count == 1
