import httpx
import pytest

from webchat.integrations.dealership import DealershipClient, DealershipError


@pytest.mark.asyncio
async def test_public_read_uses_filters_without_api_key_and_normalizes_assets() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["pageSize"] == "6"
        assert "X-API-Key" not in request.headers
        return httpx.Response(
            200,
            json={"items": [{"id": "veh-001", "images": ["/assets/vehicles/veh-001.jpg"]}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://platform") as http:
        client = DealershipClient("http://platform", "secret", http)
        result = await client.search_vehicles({"pageSize": 6})

    assert result["items"][0]["images"][0] == "http://platform/assets/vehicles/veh-001.jpg"


@pytest.mark.asyncio
async def test_vehicle_search_preserves_repeated_negative_filter_values() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list("excludedMakes") == ["Land Rover", "BMW"]
        return httpx.Response(200, json={"items": [], "pagination": {"totalItems": 0}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://platform"
    ) as http:
        client = DealershipClient("http://platform", "secret", http)
        await client.search_vehicles({"excludedMakes": ["Land Rover", "BMW"]})


@pytest.mark.asyncio
async def test_structured_platform_error_is_normalized() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            404,
            json={"error": {"code": "NOT_FOUND", "message": "Missing", "retryable": False}},
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://platform") as http:
        client = DealershipClient("http://platform", "secret", http)
        with pytest.raises(DealershipError) as caught:
            await client.get_vehicle("veh-999")

    assert caught.value.code == "NOT_FOUND"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_platform_field_errors_are_preserved() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            422,
            json={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Some contact details are invalid.",
                    "retryable": False,
                    "fieldErrors": {"phone": "Enter a valid phone number."},
                }
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://platform") as http:
        client = DealershipClient("http://platform", "secret", http)
        with pytest.raises(DealershipError) as caught:
            await client.create_test_drive({}, "request-1")

    assert caught.value.field_errors == {"phone": "Enter a valid phone number."}


@pytest.mark.asyncio
async def test_workshop_reconciliation_read_stays_server_authenticated() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/workshop-bookings/wsb-001"
        assert request.headers["X-API-Key"] == "secret"
        return httpx.Response(
            200,
            json={"id": "wsb-001", "status": "cancelled", "reference": "WORK-1"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://platform"
    ) as http:
        client = DealershipClient("http://platform", "secret", http)
        result = await client.get_workshop_booking("wsb-001")

    assert result["status"] == "cancelled"


@pytest.mark.asyncio
async def test_vehicle_image_is_fetched_only_from_platform_asset_path() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/vehicles/veh-001":
            return httpx.Response(
                200,
                json={"id": "veh-001", "images": ["/assets/vehicles/veh-001.jpg"]},
            )
        assert request.url.path == "/assets/vehicles/veh-001.jpg"
        return httpx.Response(200, content=b"image", headers={"Content-Type": "image/jpeg"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://platform"
    ) as http:
        client = DealershipClient("http://platform", "secret", http)
        content, media_type = await client.get_vehicle_image("veh-001")

    assert content == b"image"
    assert media_type == "image/jpeg"
