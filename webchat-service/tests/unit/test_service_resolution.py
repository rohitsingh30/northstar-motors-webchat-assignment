import pytest

from webchat.orchestration.tools.service_resolution import resolve_live_service

SERVICES = [
    {
        "id": "brake-inspection",
        "name": "Brake inspection",
        "description": "Brake condition and performance inspection.",
    },
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
        ("brakes", "brake-inspection"),
        ("please check my brake pads", "brake-inspection"),
    ],
)
def test_arbitrary_service_wording_resolves_against_live_catalogue(
    wording: str, service_id: str
) -> None:
    resolution = resolve_live_service(SERVICES, wording)

    assert resolution.status == "matched"
    assert resolution.service is not None
    assert resolution.service["id"] == service_id


def test_unknown_service_is_distinct_from_an_ambiguous_match() -> None:
    resolution = resolve_live_service(SERVICES, "Do you do car cleaning?")

    assert resolution.status == "unavailable"
    assert resolution.service is None


def test_equal_live_service_matches_return_candidates_instead_of_guessing() -> None:
    resolution = resolve_live_service(
        [
            {"id": "tyre-fitting", "name": "Tyre fitting"},
            {"id": "tyre-service", "name": "Tyre service"},
        ],
        "tyre",
    )

    assert resolution.status == "ambiguous"
    assert resolution.service is None
    assert {item["id"] for item in resolution.candidates} == {
        "tyre-fitting",
        "tyre-service",
    }
