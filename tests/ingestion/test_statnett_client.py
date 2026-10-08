"""Verify hourly request parameters, raw responses, and client error handling."""

import json

import httpx
import pytest

from oslo_energy.ingestion.statnett_client import StatnettClient, StatnettClientError


def test_get_production_consumption_preserves_raw_response():
    """An hourly request preserves the complete decoded response payload."""
    payload = {"Production": [1.5, None], "Consumption": [2.0, 3.0], "metadata": "raw"}

    def respond(request):
        """Assert the GET endpoint, hourly bounds, and timeout before returning raw data."""
        assert request.method == "GET"
        assert str(request.url.copy_with(query=None)) == (
            "https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData"
        )
        assert request.url.params["FromInTicks"] == "1104534000000"
        assert request.url.params["Frequency"] == "Hours"
        assert int(request.url.params["ToInTicks"]) > 1104534000000
        assert set(request.url.params) == {"FromInTicks", "ToInTicks", "Frequency"}
        assert request.extensions["timeout"]["read"] == 12
        return httpx.Response(200, json=payload)

    client = StatnettClient(timeout=12, transport=httpx.MockTransport(respond))

    assert client.get_production_consumption("2005-01-01") == payload


def test_custom_base_url():
    """A custom base URL determines the request endpoint without changing the response."""
    def respond(request):
        """Assert the custom endpoint and return an empty JSON object."""
        assert str(request.url.copy_with(query=None)) == (
            "https://example.test/api/ProductionConsumption/GetData"
        )
        return httpx.Response(200, json={})

    client = StatnettClient(
        base_url="https://example.test/api/", transport=httpx.MockTransport(respond)
    )

    assert client.get_production_consumption("2025-01-01") == {}


@pytest.mark.parametrize(
    "day,start,end", [("2025-03-30", "2025-03-29T23:00:00+00:00", "2025-03-30T21:59:59.999000+00:00"),
                      ("2025-10-26", "2025-10-25T22:00:00+00:00", "2025-10-26T22:59:59.999000+00:00")],
)
def test_bounded_hourly_request_uses_local_dst_day(day, start, end):
    """Hourly request bounds span the full Oslo day across both DST transitions."""
    from datetime import datetime

    def respond(request):
        """Assert the expected UTC millisecond bounds and hourly frequency."""
        assert int(request.url.params["FromInTicks"]) == int(datetime.fromisoformat(start).timestamp() * 1000)
        assert int(request.url.params["ToInTicks"]) == int(datetime.fromisoformat(end).timestamp() * 1000)
        assert request.url.params["Frequency"] == "Hours"
        return httpx.Response(200, json={})

    client = StatnettClient(transport=httpx.MockTransport(respond))
    assert client.get_production_consumption(day, to_date=day) == {}


def test_reversed_date_range_fails_before_request():
    """A reversed date range raises ValueError before sending a request."""
    def respond(request):
        """Fail if range validation allows an HTTP request."""
        pytest.fail("Invalid range must not send an HTTP request")

    client = StatnettClient(transport=httpx.MockTransport(respond))
    with pytest.raises(ValueError, match="precede"):
        client.get_production_consumption("2025-02-01", to_date="2025-01-01")


@pytest.mark.parametrize("status_code", [400, 500])
def test_http_failure(status_code):
    """HTTP error responses are reported as StatnettClientError."""
    client = StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code))
    )

    with pytest.raises(StatnettClientError, match="Statnett request failed"):
        client.get_production_consumption("2005-01-01")


def test_network_failure():
    """A read timeout becomes StatnettClientError with the timeout message."""
    def respond(request):
        """Raise a read timeout associated with the intercepted request."""
        raise httpx.ReadTimeout("Timed out", request=request)

    client = StatnettClient(transport=httpx.MockTransport(respond))

    with pytest.raises(StatnettClientError, match="Timed out"):
        client.get_production_consumption("2005-01-01")


def test_invalid_json():
    """A successful HTTP response containing invalid JSON raises a client error."""
    client = StatnettClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not json")
        )
    )

    with pytest.raises(StatnettClientError, match="invalid JSON"):
        client.get_production_consumption("2005-01-01")


@pytest.mark.parametrize("payload", [[], None, "unexpected", 42])
def test_non_object_response(payload):
    """Decoded JSON values other than objects are rejected with a client error."""
    client = StatnettClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        )
    )

    with pytest.raises(StatnettClientError, match="JSON object"):
        client.get_production_consumption("2005-01-01")