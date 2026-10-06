"""HTTP client for Statnett production and consumption data."""

from typing import Any

import httpx


class StatnettClientError(Exception):
    """A Statnett request or response could not be processed."""


class StatnettClient:
    def __init__(
        self,
        base_url: str = "https://driftsdata.statnett.no/restapi",
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def get_production_consumption(self, from_date: str) -> dict[str, Any]:
        url = f"{self.base_url}/ProductionConsumption/GetData"

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.get(url, params={"From": from_date})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise StatnettClientError(f"Statnett request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise StatnettClientError("Statnett returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise StatnettClientError("Statnett response must be a JSON object")

        return data