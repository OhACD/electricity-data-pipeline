"""HTTP client for Statnett production and consumption data."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx


class StatnettClientError(Exception):
    """A Statnett request or response could not be processed."""


class StatnettClient:
    """Request hourly Statnett data without transforming provider values."""

    def __init__(
        self,
        base_url: str = "https://driftsdata.statnett.no/restapi",
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ):
        """Configure the API base URL, timeout and optional HTTP transport."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def get_production_consumption(
        self, from_date: str, *, to_date: str | None = None
    ) -> dict[str, Any]:
        """Fetch hourly data from Oslo midnight using Unix-millisecond bounds.

        The optional ISO ``to_date`` is inclusive in Europe/Oslo; the end
        boundary is capped at request time. The returned JSON object retains
        provider values and metadata without unit conversion or normalization.

        Raises:
            ValueError: A date is invalid, the range is reversed, or its start
                is in the future.
            StatnettClientError: HTTP fails, JSON is invalid, or the response
                is not a JSON object.
        """
        url = f"{self.base_url}/ProductionConsumption/GetData"
        local_timezone = ZoneInfo("Europe/Oslo")
        start_date = date.fromisoformat(from_date)
        start = datetime.combine(start_date, time(), local_timezone)
        end = datetime.now(timezone.utc)
        if to_date is not None:
            end_date = date.fromisoformat(to_date)
            if end_date < start_date:
                raise ValueError("to_date must not precede from_date")
            boundary = datetime.combine(end_date + timedelta(days=1), time(), local_timezone)
            end = min(end, boundary - timedelta(milliseconds=1))
        if start > end:
            raise ValueError("from_date must not be in the future")
        params = {
            "FromInTicks": int(start.timestamp() * 1000),
            "ToInTicks": int(end.timestamp() * 1000),
            "Frequency": "Hours",
        }

        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.get(url, params=params)
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