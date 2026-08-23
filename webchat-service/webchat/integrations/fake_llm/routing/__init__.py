"""Language-specific deterministic routing used only by the fake provider."""

from .router import DeterministicApplicationRouter
from .support import SupportRouter
from .vehicle import VehicleRouter
from .workshop import WorkshopRouter

__all__ = [
    "DeterministicApplicationRouter",
    "SupportRouter",
    "VehicleRouter",
    "WorkshopRouter",
]
