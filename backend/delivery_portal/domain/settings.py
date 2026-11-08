"""The pizzeria's delivery settings, edited in the portal (Settings page)."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional

from .geo import EnuDatum, GeoPoint, haversine_m
from .tickets import TicketError

_MAP_ID = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
MAX_SHOP_DISTANCE_M = 1000.0   # the parking spot belongs to this pizzeria


@dataclass(frozen=True)
class DeliverySettings:
    """
    datum: the pizzeria, origin of the shared ENU frame. Every robot gets it with each
    startTrip, so the portal and the robots can never disagree.
    shop: where the robots park (return trips end there); usually the datum.
    parked_heading_deg: compass bearing (0 = North, clockwise) the robots face when parked;
    it lets a trip align to GPS before moving. None: unknown (they align once driving).
    """
    datum: GeoPoint
    shop: GeoPoint
    enu_map_id: str = 'enu_pizzeria'
    parked_heading_deg: Optional[float] = None
    cargo_actions: bool = False
    auto_return: bool = True

    def __post_init__(self):
        if not _MAP_ID.fullmatch(self.enu_map_id or ''):
            raise TicketError(f'{self.enu_map_id!r} is not a map id (A-Z a-z 0-9 _ . : -)')
        h = self.parked_heading_deg
        if h is not None and (not math.isfinite(h) or not 0.0 <= h < 360.0):
            raise TicketError('the parked heading is a compass bearing, 0 to 359 degrees')
        if haversine_m(self.datum, self.shop) > MAX_SHOP_DISTANCE_M:
            raise TicketError(f'the parking spot is more than {MAX_SHOP_DISTANCE_M:.0f} m from '
                              'the pizzeria')

    @property
    def frame_id(self) -> str:
        """
        The VDA 5050 mapId on the wire: enu_map_id plus a fingerprint of the location. After a
        move, a robot still reporting in the old frame (until its next startTrip) no longer
        matches, so it is never drawn at the new pizzeria by mistake.
        """
        tag = hashlib.sha1(f'{self.datum.lat:.7f},{self.datum.lon:.7f}'.encode()).hexdigest()[:6]
        return f'{self.enu_map_id}-{tag}'

    @property
    def enu(self) -> EnuDatum:
        return EnuDatum(self.datum, self.frame_id)

    def to_dict(self) -> dict:
        return {'datum': [self.datum.lat, self.datum.lon], 'shop': [self.shop.lat, self.shop.lon],
                'enu_map_id': self.enu_map_id, 'parked_heading_deg': self.parked_heading_deg,
                'cargo_actions': self.cargo_actions, 'auto_return': self.auto_return}

    @staticmethod
    def from_dict(d: dict) -> 'DeliverySettings':
        try:
            heading = d.get('parked_heading_deg')
            return DeliverySettings(
                datum=GeoPoint(*map(float, d['datum'])), shop=GeoPoint(*map(float, d['shop'])),
                enu_map_id=str(d.get('enu_map_id') or 'enu_pizzeria'),
                parked_heading_deg=None if heading in (None, '') else float(heading),
                cargo_actions=bool(d.get('cargo_actions', False)),
                auto_return=bool(d.get('auto_return', True)))
        except (KeyError, TypeError, ValueError) as e:
            raise TicketError(f'invalid settings: {e}') from e
