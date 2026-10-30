"""Application composition helpers."""

from __future__ import annotations

from .ports import CompanionRuntimePort
from .service import CompanionApplicationService


def create_application_service(
    runtime: CompanionRuntimePort,
) -> CompanionApplicationService:
    return CompanionApplicationService(runtime)
