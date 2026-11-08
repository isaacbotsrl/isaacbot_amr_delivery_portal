"""Trip time estimates for a sidewalk robot (no traffic model: it drives at its own pace)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .geo import EnuPoint, polyline_length


@dataclass(frozen=True)
class EtaEstimator:
    speed: float = 0.8          # m/s, the edges' maxSpeed
    efficiency: float = 0.75    # crossings, slow-downs around people, re-plans
    handoff_s: float = 120.0    # time at the customer

    def __post_init__(self):
        if self.speed <= 0 or not 0 < self.efficiency <= 1:
            raise ValueError('speed must be > 0 and efficiency in (0, 1]')

    def drive_s(self, distance_m: float) -> float:
        return max(0.0, distance_m) / (self.speed * self.efficiency)

    def trip_s(self, distance_m: float) -> float:
        """Shop -> customer, ready to hand off."""
        return self.drive_s(distance_m)

    def round_trip_s(self, distance_m: float) -> float:
        return 2 * self.drive_s(distance_m) + self.handoff_s

    def remaining_s(self, waypoints: Sequence[EnuPoint], reached_index: int,
                    position: EnuPoint = None) -> float:
        """
        Time left to the last waypoint, given the last reached one (index into waypoints) and,
        when known, the robot's position between it and the next.
        """
        if not waypoints:
            return 0.0
        reached_index = max(0, min(reached_index, len(waypoints) - 1))
        rest = list(waypoints[reached_index + 1:])
        if not rest:
            return 0.0
        head = position if position is not None else waypoints[reached_index]
        return self.drive_s(head.distance_to(rest[0]) + polyline_length(rest))
