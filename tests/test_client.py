import httpx
import pytest

from polestar import client as client_mod
from polestar.client import PolestarClient


@pytest.fixture
def make(monkeypatch):
    """A client over a scripted fake transport: `script(request)` returns the response. No network, no .env."""
    monkeypatch.setattr(client_mod, "load_dotenv", lambda: None)
    monkeypatch.delenv("ACCOUNT_ID", raising=False)
    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", sleeps.append)

    def build(script):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"accessToken": f"tok{sum(c.url.path.endswith('/token') for c in calls)}",
                                                 "expiresIn": 3600})
            return script(request)

        http = httpx.Client(transport=httpx.MockTransport(handler))
        return PolestarClient("https://api.test/token", "app", "secret", http), calls, sleeps

    return build


def gets(calls):
    return [c for c in calls if not c.url.path.endswith("/token")]


def test_base_url_strips_token_suffix_and_account_id_defaults_to_app_id(make):
    c, _, _ = make(lambda r: httpx.Response(200, json={}))
    assert c.base == "https://api.test" and c.account_id == "app"


def test_token_is_cached_across_calls(make):
    c, calls, _ = make(lambda r: httpx.Response(200, json={"data": []}))
    c.vehicles()
    c.vehicles()
    assert len(calls) - len(gets(calls)) == 1


def test_request_carries_bearer_and_client_id(make):
    c, calls, _ = make(lambda r: httpx.Response(200, json={}))
    c.vehicles()
    req = gets(calls)[0]
    assert req.headers["authorization"] == "Bearer tok1" and req.headers["x-client-id"] == "app"


def test_domain_paths_use_kebab_case_and_charging_prefix(make):
    c, calls, _ = make(lambda r: httpx.Response(200, json={}))
    c.telemetry("V", "parkingClimatization")
    c.telemetry("V", "targetSoc")
    c.telemetry("V", "battery")
    assert [r.url.path for r in gets(calls)] == [
        "/v1/vehicles/V/telemetry/parking-climatization", "/v1/vehicles/V/charging/target-soc", "/v1/vehicles/V/telemetry/battery"]


def test_503_is_retried_with_backoff(make):
    answers = iter([503, 502, 200])
    c, calls, sleeps = make(lambda r: httpx.Response(next(answers), json={"ok": True}))
    assert c.vehicles() == {"ok": True}
    assert sleeps == [1, 2]


def test_429_honours_retry_after_capped_at_a_minute(make):
    answers = iter([(429, {"Retry-After": "3"}), (429, {"Retry-After": "999"}), (200, {})])
    c, _, sleeps = make(lambda r: httpx.Response((a := next(answers))[0], headers=a[1], json={}))
    c.vehicles()
    assert sleeps == [3, 60]


def test_401_fetches_a_new_token_and_retries(make):
    c, calls, _ = make(lambda r: httpx.Response(401 if r.headers["authorization"] == "Bearer tok1" else 200, json={"ok": 1}))
    assert c.vehicles() == {"ok": 1}
    assert [r.headers["authorization"] for r in gets(calls)] == ["Bearer tok1", "Bearer tok2"]


def test_404_raises_without_retry(make):
    c, calls, sleeps = make(lambda r: httpx.Response(404, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        c.telemetry("V", "chargeLocations")
    assert len(gets(calls)) == 1 and sleeps == []


def test_gives_up_after_three_server_errors(make):
    c, calls, _ = make(lambda r: httpx.Response(503, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        c.vehicles()
    assert len(gets(calls)) == 3
