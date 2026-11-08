"""Association requests and immutable results crossing the catalog worker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from .types import ApplicationListing


class AssociationStatus(Enum):
    PENDING = "pending"
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AssociationKey:
    lookup: Literal["default", "recommended", "all", "name"]
    content_type: str


@dataclass(frozen=True, slots=True)
class AssociationRead:
    status: AssociationStatus
    handles: tuple[object, ...] = ()
    application_id: str = ""
    name: str | None = None


@dataclass(frozen=True, slots=True)
class AssociationResolution:
    status: AssociationStatus
    listing: ApplicationListing | None = None
    name: str | None = None


AssociationReads = tuple[tuple[AssociationKey, AssociationRead], ...]
