import pytest

from webchat.orchestration.service_resolution import match_live_service

SERVICES = [
    {
        "id": "full-service",
        "name": "Full service",
        "description": "Comprehensive annual vehicle service.",
    },
    {
        "id": "diagnostic",
        "name": "Diagnostic inspection",
        "description": "Investigation of a warning light or fault.",
    },
    {
        "id": "tyre-fitting",
        "name": "Tyre fitting",
        "description": "Tyre replacement and balancing.",
    },
]


@pytest.mark.parametrize(
    ("wording", "service_id"),
    [
        ("What would I pay to have my tyres fitted?", "tyre-fitting"),
        ("How long would a warning-light diagnosis take?", "diagnostic"),
        ("What does the annual maintenance include?", "full-service"),
        ("I want to do tyres", "tyre-fitting"),
    ],
)
def test_arbitrary_service_wording_resolves_against_live_catalogue(
    wording: str, service_id: str
) -> None:
    assert match_live_service(SERVICES, wording)["id"] == service_id
