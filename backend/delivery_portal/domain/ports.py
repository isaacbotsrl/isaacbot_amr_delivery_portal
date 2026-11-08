"""Ports: what the application needs from the outside world (adapters in infrastructure/)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from .geo import GeoPoint
from .robots import RobotIdentity
from .tickets import Ticket


class RoutingError(RuntimeError):
    """The router could not produce a route (down, no path, bad key)."""


@dataclass(frozen=True)
class RoutedPath:
    points: List[GeoPoint]   # start ... destination
    distance_m: float        # the router's length (walking network)
    provider: str


class RoutingProvider(ABC):
    @abstractmethod
    def route(self, start: GeoPoint, end: GeoPoint) -> RoutedPath:
        """A pedestrian route; raises RoutingError."""


@dataclass(frozen=True)
class GeocodeResult:
    label: str
    point: GeoPoint


class Geocoder(ABC):
    @abstractmethod
    def search(self, query: str, near: Optional[GeoPoint] = None,
               limit: int = 5) -> List[GeocodeResult]:
        """Addresses matching the query, nearest to `near` first when the service can."""


class FleetSource(ABC):
    @abstractmethod
    def robots(self) -> List[RobotIdentity]:
        """The fleet's robots (the RMF site manager's list)."""


class OrderPublisher(ABC):
    @abstractmethod
    def publish_order(self, robot: RobotIdentity, order: Dict) -> None:
        """Send a VDA 5050 order body (the adapter adds the header)."""

    @abstractmethod
    def publish_instant_actions(self, robot: RobotIdentity, actions: Dict) -> None:
        """Send a VDA 5050 instantActions body."""


class TicketRepository(ABC):
    @abstractmethod
    def save(self, ticket: Ticket) -> None: ...

    @abstractmethod
    def get(self, ticket_id: str) -> Optional[Ticket]: ...

    @abstractmethod
    def list(self, include_closed_since: Optional[float] = None) -> List[Ticket]:
        """Open tickets, plus closed ones updated after include_closed_since (None: all)."""


class SettingsRepository(ABC):
    @abstractmethod
    def load(self) -> Optional[Dict]:
        """The stored settings (DeliverySettings.to_dict()), or None before the first save."""

    @abstractmethod
    def save(self, settings: Dict) -> None: ...
