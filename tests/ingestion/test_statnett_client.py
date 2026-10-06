import json

import httpx
import pytest

from oslo_energy.ingestion.statnett_client import StatnettClient, StatnettClientError


def test_get_production_consumption_preserves_raw_response():
    payload = {"Production": [1.5, None], "Consumption": [2.0, 3.0], "metadata": "raw"}

    def respond(request):
        assert request.method == "GET"
        assert str(request.url.copy_with(query=None)) == (
            "https://driftsdata.statnett.no/restapi/ProductionConsumption/GetData"
        )
        assert dict(request.url.params) == {"From": "2005-01-01"}
        assert request.extensions["timeout"]["read"] == 12
        return httpx.Response(200, json=payload)

    client = StatnettClient(timeout=12, transport=httpx.MockTransport(respond))

    assert client.get_production_consumption("2005-01-01") == payload


def test_custom_base_url():
    def respond(request):
        assert str(request.url.copy_with(query=None)) == (
            "https://example.test/api/ProductionConsumption/GetData"
        )
        return httpx.Response(200, json={})

    client = StatnettClient(
        base_url="https://example.test/api/", transport=httpx.MockTransport(respond)
    )

    assert client.get_production_consumption("2025-01-01") == {}


@pytest.mark.parametrize("status_code", [400, 500])
def test_http_failure(status_code):
    client = StatnettClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code))
    )

    with pytest.raises(StatnettClientError, match="Statnett request failed"):
        client.get_production_consumption("2005-01-01")


def test_network_failure():
    def respond(request):
        raise httpx.ReadTimeout("Timed out", request=request)

    client = StatnettClient(transport=httpx.MockTransport(respond))

    with pytest.raises(StatnettClientError, match="Timed out"):
        client.get_production_consumption("2005-01-01")


def test_invalid_json():
    client = StatnettClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not json")
        )
    )

    with pytest.raises(StatnettClientError, match="invalid JSON"):
        client.get_production_consumption("2005-01-01")


@pytest.mark.parametrize("payload", [[], None, "unexpected", 42])
def test_non_object_response(payload):
    client = StatnettClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        )
    )

    with pytest.raises(StatnettClientError, match="JSON object"):
        client.get_production_consumption("2005-01-01")