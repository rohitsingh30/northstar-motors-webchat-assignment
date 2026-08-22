from __future__ import annotations


def money(pence: int | None) -> str:
    if pence is None:
        return "Price on request"
    return f"£{pence / 100:,.2f}"


def availability_text(state: str) -> str:
    return {
        "available": "Available for enquiry and eligible test-drive slots.",
        "reserved": "Reserved; interest can be registered, but a test drive cannot be booked.",
        "sold": "Sold; a sales enquiry can still be sent, but no test drive or interest can be registered.",
    }.get(state, "Availability is unknown; please ask the dealership.")
