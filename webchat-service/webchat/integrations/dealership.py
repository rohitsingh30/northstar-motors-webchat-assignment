from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


class DealershipError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        field_errors: dict[str, str] | None = None,
        recovery: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable
        self.field_errors = field_errors or {}
        self.recovery = recovery or {}


class DealershipClient:
    """Typed boundary for public reads from the authoritative dealership platform."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(8, connect=3),
            headers={"Accept": "application/json"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _normalize_assets(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._normalize_assets(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._normalize_assets(item) for item in value]
        if isinstance(value, str) and value.startswith("/assets/"):
            return urljoin(self.base_url, value.lstrip("/"))
        return value

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        protected: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"X-API-Key": self.api_key} if protected else {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._client.request(
                method, path, params=params, json=body, headers=headers
            )
        except httpx.TimeoutException as error:
            raise DealershipError(
                503, "PLATFORM_TIMEOUT", "The dealership platform timed out.", True
            ) from error
        except httpx.HTTPError as error:
            raise DealershipError(
                503, "PLATFORM_UNAVAILABLE", "The dealership platform is unavailable.", True
            ) from error
        if response.status_code >= 400:
            try:
                problem = response.json().get("error", {})
            except ValueError:
                problem = {}
            raise DealershipError(
                response.status_code,
                str(problem.get("code", "PLATFORM_ERROR")),
                str(
                    problem.get(
                        "message", "The dealership platform could not complete the request."
                    )
                ),
                bool(problem.get("retryable", response.status_code >= 500)),
                {
                    str(field): str(message)
                    for field, message in problem.get("fieldErrors", {}).items()
                }
                if isinstance(problem.get("fieldErrors"), dict)
                else None,
            )
        if int(response.headers.get("content-length", "0")) > 2_000_000:
            raise DealershipError(
                502, "PLATFORM_RESPONSE_TOO_LARGE", "The platform response was too large."
            )
        return self._normalize_assets(response.json())

    async def search_vehicles(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._get("/api/vehicles", filters)

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/api/vehicles/{vehicle_id}")

    async def get_vehicle_availability(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/api/vehicles/{vehicle_id}/availability")

    async def get_vehicle_image(self, vehicle_id: str) -> tuple[bytes, str]:
        vehicle = await self.get_vehicle(vehicle_id)
        image_url = next(iter(vehicle.get("images") or []), None)
        parsed_image = urlsplit(str(image_url or ""))
        parsed_base = urlsplit(self.base_url)
        if (
            parsed_image.scheme not in {"http", "https"}
            or parsed_image.hostname != parsed_base.hostname
            or not parsed_image.path.startswith("/assets/vehicles/")
        ):
            raise DealershipError(404, "IMAGE_NOT_FOUND", "Vehicle image not found.")
        try:
            response = await self._client.get(parsed_image.path)
        except httpx.HTTPError as error:
            raise DealershipError(
                503, "PLATFORM_UNAVAILABLE", "The dealership platform is unavailable.", True
            ) from error
        if response.status_code != 200 or len(response.content) > 5_000_000:
            raise DealershipError(404, "IMAGE_NOT_FOUND", "Vehicle image not found.")
        media_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
        if not media_type.startswith("image/"):
            raise DealershipError(404, "IMAGE_NOT_FOUND", "Vehicle image not found.")
        return response.content, media_type

    async def list_offers(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._get("/api/offers", filters)

    async def get_offer(self, offer_id: str) -> dict[str, Any]:
        return await self._get(f"/api/offers/{offer_id}")

    async def list_dealerships(self) -> dict[str, Any]:
        return await self._get("/api/dealerships")

    async def get_dealership(self, dealership_id: str) -> dict[str, Any]:
        return await self._get(f"/api/dealerships/{dealership_id}")

    async def get_opening_hours(self, dealership_id: str) -> dict[str, Any]:
        return await self._get(f"/api/dealerships/{dealership_id}/opening-hours")

    async def list_service_types(self) -> dict[str, Any]:
        return await self._get("/api/service-types")

    async def list_test_drive_slots(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._get("/api/test-drive-slots", filters)

    async def list_workshop_locations(self) -> dict[str, Any]:
        return await self._get("/api/workshop-locations")

    async def list_workshop_slots(self, filters: dict[str, Any]) -> dict[str, Any]:
        return await self._get("/api/workshop-availability", filters)

    async def get_business_information(self) -> dict[str, Any]:
        return await self._get("/api/business-information")

    async def create_sales_enquiry(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/sales-enquiries", body=body, protected=True, idempotency_key=key
        )

    async def create_test_drive(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/test-drive-bookings", body=body, protected=True, idempotency_key=key
        )

    async def create_vehicle_interest(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/vehicle-interests", body=body, protected=True, idempotency_key=key
        )

    async def create_callback(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/callback-requests", body=body, protected=True, idempotency_key=key
        )

    async def create_workshop_booking(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/workshop-bookings", body=body, protected=True, idempotency_key=key
        )

    async def lookup_workshop_booking(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/workshop-bookings/lookup", body=body, protected=True
        )

    async def get_workshop_booking(self, record_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/workshop-bookings/{record_id}", protected=True)

    async def update_workshop_booking(self, record_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/api/workshop-bookings/{record_id}", body=body, protected=True
        )

    async def cancel_workshop_booking(self, record_id: str) -> dict[str, Any]:
        return await self._request(
            "DELETE", f"/api/workshop-bookings/{record_id}", body={}, protected=True
        )

    async def create_dealership_message(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/dealership-messages", body=body, protected=True, idempotency_key=key
        )

    async def create_part_exchange(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/part-exchange-valuations", body=body, protected=True, idempotency_key=key
        )

    async def estimate_part_exchange(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/part-exchange-estimate", body=body, protected=True)
