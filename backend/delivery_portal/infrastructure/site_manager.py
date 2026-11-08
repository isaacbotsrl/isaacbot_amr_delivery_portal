"""
The fleet list from the RMF site manager (GET <SITE_MANAGER_URL>/api/robots, HTTP basic auth).

A robot is a *delivery* robot (the portal may send it orders) when the site manager flags it
(`delivery: true`) or its name is in DELIVERY_ROBOTS. Others stay with RMF: two masters sending
VDA 5050 orders to one robot would fight.
"""

from __future__ import annotations

from typing import Iterable, List

from ..domain.ports import FleetSource
from ..domain.robots import RobotIdentity
from .http import request_json


class SiteManagerFleet(FleetSource):
    def __init__(self, base_url: str, password: str = '', delivery_robots: Iterable[str] = (),
                 fetch=request_json):
        self.base_url, self.password = base_url.rstrip('/'), password
        self.delivery_robots = {n.strip() for n in delivery_robots if n.strip()}
        self.fetch = fetch

    def robots(self) -> List[RobotIdentity]:
        data = self.fetch(f'{self.base_url}/api/robots', basic_password=self.password,
                          timeout=5.0)
        out = []
        for r in (data or {}).get('robots') or []:
            name, serial = r.get('name'), r.get('serial_number')
            if not name or not serial:
                continue
            out.append(RobotIdentity(
                name=name, serial_number=serial,
                manufacturer=r.get('manufacturer') or 'IsaacBot',
                delivery=bool(r.get('delivery')) or name in self.delivery_robots))
        return out


class StaticFleet(FleetSource):
    """A fixed list (FLEET=name:serial[:manufacturer],... for simulation without RMF)."""

    def __init__(self, spec: str):
        self._robots = []
        for item in (spec or '').split(','):
            fields = [f.strip() for f in item.split(':')]
            if len(fields) >= 2 and fields[0] and fields[1]:
                self._robots.append(RobotIdentity(fields[0], fields[1],
                                                  fields[2] if len(fields) > 2 else 'IsaacBot'))

    def robots(self) -> List[RobotIdentity]:
        return list(self._robots)
