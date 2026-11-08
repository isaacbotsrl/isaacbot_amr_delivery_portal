"""
Fleet robots as the portal sees them: identity (from the RMF site manager) plus the live VDA 5050
state each publishes on uagv/v2/<manufacturer>/<serial>/state.

Where a robot is on the map, best first:
  1. agvPosition in the shared ENU frame (its lidar SLAM pose, aligned on board) -> lat/lon;
  2. its raw GPS fix (`information` entry infoType "gpsFix"), shown as a fainter marker;
  3. unknown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from .geo import EnuDatum, EnuPoint, GeoPoint

TRAIL_POINTS = 600   # ~10 min at one state per second


@dataclass(frozen=True)
class RobotIdentity:
    name: str
    serial_number: str
    manufacturer: str = 'IsaacBot'
    delivery: bool = True     # may the portal command it (not RMF)

    @property
    def topic_prefix(self) -> str:
        return f'uagv/v2/{self.manufacturer}/{self.serial_number}'


@dataclass(frozen=True)
class ActionState:
    action_id: str
    action_type: str
    status: str               # WAITING / INITIALIZING / RUNNING / PAUSED / FINISHED / FAILED
    result: str = ''


@dataclass(frozen=True)
class RobotStatus:
    """What one VDA 5050 state message says, reduced to what the portal shows and uses."""
    received_at: float = 0.0
    connection: str = ''              # ONLINE / OFFLINE / CONNECTIONBROKEN ('' = never heard)
    position: Optional[GeoPoint] = None         # aligned SLAM pose (ENU frame) as lat/lon
    position_enu: Optional[EnuPoint] = None
    theta: Optional[float] = None               # ENU yaw, rad
    localization_score: Optional[float] = None
    gps: Optional[GeoPoint] = None              # raw GNSS fix
    gps_fix_type: str = ''
    gps_hdop: Optional[float] = None
    map_id: str = ''
    battery: Optional[float] = None             # %
    charging: bool = False
    operating_mode: str = ''
    e_stop: str = ''                            # AUTOACK / MANUAL / REMOTE / NONE
    field_violation: bool = False
    driving: bool = False
    paused: bool = False
    order_id: str = ''
    last_node_id: str = ''
    last_node_sequence_id: int = 0
    remaining_node_ids: List[str] = field(default_factory=list)
    actions: List[ActionState] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    @property
    def map_position(self) -> Optional[GeoPoint]:
        return self.position or self.gps

    def action(self, action_id: str) -> Optional[ActionState]:
        return next((a for a in self.actions if a.action_id == action_id), None)


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _gps_from_information(info: List[Dict]) -> Dict:
    for entry in info or []:
        if not isinstance(entry, dict) or entry.get('infoType') != 'gpsFix':
            continue
        refs = {r.get('referenceKey'): r.get('referenceValue')
                for r in entry.get('infoReferences') or [] if isinstance(r, dict)}
        lat, lon = _num(refs.get('lat')), _num(refs.get('lon'))
        if lat is None or lon is None:
            return {}
        try:
            point = GeoPoint(lat, lon)
        except ValueError:
            return {}
        return {'gps': point, 'gps_fix_type': str(refs.get('fixType') or ''),
                'gps_hdop': _num(refs.get('hdop'))}
    return {}


def parse_state(state: Dict, datum: EnuDatum, received_at: float,
                previous: Optional[RobotStatus] = None) -> RobotStatus:
    """A VDA 5050 2.0 state message -> RobotStatus (lenient: missing fields stay empty)."""
    pos = state.get('agvPosition') or {}
    position = position_enu = theta = score = None
    map_id = str(pos.get('mapId') or '')
    x, y = _num(pos.get('x')), _num(pos.get('y'))
    if (pos.get('positionInitialized') and map_id == datum.map_id
            and x is not None and y is not None):
        position_enu = EnuPoint(x, y)
        position = datum.to_geo(position_enu)
        theta = _num(pos.get('theta'))
        score = _num(pos.get('localizationScore'))
    battery = state.get('batteryState') or {}
    safety = state.get('safetyState') or {}
    actions = [ActionState(str(a.get('actionId', '')), str(a.get('actionType', '')),
                           str(a.get('actionStatus', '')), str(a.get('resultDescription') or ''))
               for a in state.get('actionStates') or [] if isinstance(a, dict)]
    errors = [{'type': str(e.get('errorType', '')), 'level': str(e.get('errorLevel', '')),
               'description': str(e.get('errorDescription') or '')}
              for e in state.get('errors') or [] if isinstance(e, dict)]
    return RobotStatus(
        received_at=received_at,
        connection=(previous.connection if previous and previous.connection else 'ONLINE'),
        position=position, position_enu=position_enu, theta=theta, localization_score=score,
        map_id=map_id,
        battery=_num(battery.get('batteryCharge')), charging=bool(battery.get('charging')),
        operating_mode=str(state.get('operatingMode') or ''),
        e_stop=str(safety.get('eStop') or ''), field_violation=bool(safety.get('fieldViolation')),
        driving=bool(state.get('driving')), paused=bool(state.get('paused')),
        order_id=str(state.get('orderId') or ''),
        last_node_id=str(state.get('lastNodeId') or ''),
        last_node_sequence_id=int(_num(state.get('lastNodeSequenceId')) or 0),
        remaining_node_ids=[str(n.get('nodeId')) for n in state.get('nodeStates') or []
                            if isinstance(n, dict)],
        actions=actions, errors=errors,
        **_gps_from_information(state.get('information') or []))


@dataclass(frozen=True)
class Robot:
    identity: RobotIdentity
    status: RobotStatus = RobotStatus()
    trail: List[GeoPoint] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def online(self) -> bool:
        return self.status.connection == 'ONLINE'

    @property
    def stopped_by_safety(self) -> bool:
        return self.status.e_stop not in ('', 'NONE')

    def with_status(self, status: RobotStatus) -> 'Robot':
        trail = self.trail
        p = status.map_position
        if p is not None and (not trail or (trail[-1].lat, trail[-1].lon) != (p.lat, p.lon)):
            trail = (trail + [p])[-TRAIL_POINTS:]
        return replace(self, status=status, trail=trail)

    def with_connection(self, connection: str) -> 'Robot':
        return replace(self, status=replace(self.status, connection=connection))
