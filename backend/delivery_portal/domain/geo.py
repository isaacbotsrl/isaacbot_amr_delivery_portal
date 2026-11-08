"""
Geodesy for the delivery portal: WGS84 lat/lon <-> a local East-North-Up frame.

The fleet shares one outdoor frame, `enu_<site>`: metres East (x) and North (y) of a fixed datum,
the pizzeria. Orders and robot positions travel in that frame; the robot itself aligns it to its
live SLAM map. The maths here must match the robot side (isaacbot_amr_vda5050_adapter
domain/geo), which is tested against the same vectors (tests/test_geo.py).

Pure Python, no dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

# WGS84
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float

    def __post_init__(self):
        if not (-90.0 <= self.lat <= 90.0) or not (-180.0 <= self.lon <= 180.0) \
                or math.isnan(self.lat) or math.isnan(self.lon):
            raise ValueError(f'not a valid lat/lon: {self.lat}, {self.lon}')


@dataclass(frozen=True)
class EnuPoint:
    x: float  # metres East of the datum
    y: float  # metres North of the datum

    def distance_to(self, other: 'EnuPoint') -> float:
        return math.hypot(other.x - self.x, other.y - self.y)

    def heading_to(self, other: 'EnuPoint') -> float:
        """ENU yaw (rad, 0 = East, counter-clockwise, REP-103) from here to other."""
        return math.atan2(other.y - self.y, other.x - self.x)


def _ecef(lat: float, lon: float, h: float = 0.0) -> Tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo),
            (n + h) * math.cos(la) * math.sin(lo),
            (n * (1 - _E2) + h) * math.sin(la))


def _geodetic(x: float, y: float, z: float) -> Tuple[float, float]:
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - _E2))
    for _ in range(6):
        n = _A / math.sqrt(1 - _E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - _E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon)


@dataclass(frozen=True)
class EnuDatum:
    """The origin of the shared ENU frame (on the ellipsoid, height 0)."""
    origin: GeoPoint
    map_id: str = 'enu_site'

    def to_enu(self, p: GeoPoint) -> EnuPoint:
        x0, y0, z0 = _ecef(self.origin.lat, self.origin.lon)
        x, y, z = _ecef(p.lat, p.lon)
        dx, dy, dz = x - x0, y - y0, z - z0
        la, lo = math.radians(self.origin.lat), math.radians(self.origin.lon)
        east = -math.sin(lo) * dx + math.cos(lo) * dy
        north = (-math.sin(la) * math.cos(lo) * dx - math.sin(la) * math.sin(lo) * dy
                 + math.cos(la) * dz)
        return EnuPoint(east, north)

    def to_geo(self, p: EnuPoint) -> GeoPoint:
        x0, y0, z0 = _ecef(self.origin.lat, self.origin.lon)
        la, lo = math.radians(self.origin.lat), math.radians(self.origin.lon)
        # ENU (up = 0 on the tangent plane) -> ECEF; then drop back onto the ellipsoid.
        dx = -math.sin(lo) * p.x - math.sin(la) * math.cos(lo) * p.y
        dy = math.cos(lo) * p.x - math.sin(la) * math.sin(lo) * p.y
        dz = math.cos(la) * p.y
        lat, lon = _geodetic(x0 + dx, y0 + dy, z0 + dz)
        return GeoPoint(lat, lon)


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    r = 6371008.8
    la1, la2 = math.radians(a.lat), math.radians(b.lat)
    dla, dlo = la2 - la1, math.radians(b.lon - a.lon)
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def polyline_length(points: Sequence[EnuPoint]) -> float:
    return sum(a.distance_to(b) for a, b in zip(points, points[1:]))


def _turn(a: EnuPoint, b: EnuPoint, c: EnuPoint) -> float:
    d = b.heading_to(c) - a.heading_to(b)
    return abs(math.atan2(math.sin(d), math.cos(d)))


def resample(points: Sequence[EnuPoint], spacing: float,
             corner_deg: float = 30.0, min_gap: float = 1.0) -> List[EnuPoint]:
    """
    Waypoints along a polyline: one every `spacing` metres, plus every vertex that turns more
    than `corner_deg` (so corners are not cut), always the first and last point, and never two
    points closer than `min_gap` (the last point wins).
    """
    if spacing <= 0:
        raise ValueError('spacing must be > 0')
    pts = [p for i, p in enumerate(points) if i == 0 or p.distance_to(points[i - 1]) > 1e-6]
    if len(pts) < 2:
        return list(pts)
    corners = {i for i in range(1, len(pts) - 1)
               if math.degrees(_turn(pts[i - 1], pts[i], pts[i + 1])) > corner_deg}
    out = [pts[0]]
    since = 0.0  # distance travelled since the last emitted point
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        seg = a.distance_to(b)
        pos = 0.0
        while since + (seg - pos) >= spacing:
            pos += spacing - since
            t = pos / seg
            out.append(EnuPoint(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y)))
            since = 0.0
        since += seg - pos
        if i in corners and out[-1].distance_to(b) > 1e-6:
            out.append(b)
            since = 0.0
    last = pts[-1]
    kept = [out[0]]
    for p in out[1:] + [last]:
        if p.distance_to(kept[-1]) >= min_gap:
            kept.append(p)
    if kept[-1] != last:  # the end crowds the previous point: the end wins
        if len(kept) > 1:
            kept[-1] = last
        else:
            kept.append(last)
    return kept


def decode_polyline(encoded: str, precision: int = 5) -> List[GeoPoint]:
    """Google encoded polyline (precision 5) / Valhalla polyline6 (precision 6)."""
    coords, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    while index < len(encoded):
        for which in range(2):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        coords.append(GeoPoint(lat / factor, lon / factor))
    return coords
