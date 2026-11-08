"""
VDA 5050 2.0 orders from a walking route.

The route (lat/lon) is converted into the shared ENU frame, resampled into waypoints every
`spacing` metres (corners kept) and emitted as a base-only order:

    start (the robot's current position) -> w1 -> ... -> destination

Node sequence ids are even (0, 2, 4, ...), edge ids odd, as VDA 5050 requires. Every node carries
the heading of travel so the robot never turns on the spot at a waypoint. Intermediate nodes get a
generous allowedDeviationXY: the robot navigates by lidar and only passes *near* them; the
destination gets its own (GPS-limited) tolerance.

The cargo actions (openCargo, confirmPickup, closeCargo) are only added when the robot supports
them (Phase 2 of the delivery plan); without them, the robot simply stops at the destination and
staff mark the ticket delivered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .geo import EnuDatum, EnuPoint, GeoPoint, polyline_length, resample

CARGO_ACTIONS = ('openCargo', 'confirmPickup', 'closeCargo')


@dataclass(frozen=True)
class OrderSettings:
    spacing_m: float = 8.0               # waypoint spacing along the route
    corner_deg: float = 30.0             # keep vertices that turn more than this
    max_speed: float = 0.8               # m/s on every edge (Nav 2 vx_max; SYS-SR-004 caps 1.0)
    waypoint_tolerance_m: float = 2.0    # allowedDeviationXY of intermediate nodes
    destination_tolerance_m: float = 3.0  # allowedDeviationXY of the last node
    cargo_actions: bool = False          # the robot supports openCargo/confirmPickup/closeCargo


@dataclass(frozen=True)
class BuiltOrder:
    order: Dict                     # VDA 5050 order body (no header: the publisher adds it)
    waypoints: List[EnuPoint]       # the nodes' positions, start first
    destination_node_id: str
    distance_m: float               # along the waypoints


def _action(order_id: str, action_type: str, blocking: str = 'HARD',
            description: str = '') -> Dict:
    return {'actionType': action_type, 'actionId': f'{order_id}-{action_type}',
            'blockingType': blocking, 'actionDescription': description, 'actionParameters': []}


def build_order(order_id: str, datum: EnuDatum, start: EnuPoint, route: Sequence[GeoPoint],
                settings: OrderSettings = OrderSettings(), start_theta: Optional[float] = None,
                with_cargo: Optional[bool] = None, start_node_id: str = 'start') -> BuiltOrder:
    """
    An order from `start` (ENU, the robot's position) along `route` (lat/lon; its first point
    is where the router snapped the start, its last the destination).
    """
    if not route:
        raise ValueError('the route is empty')
    if not order_id:
        raise ValueError('order_id is required')
    enu = [start] + [datum.to_enu(p) for p in route]
    points = resample(enu, settings.spacing_m, settings.corner_deg)
    if len(points) < 2:  # already there: still a valid two-node order
        points = [start, datum.to_enu(route[-1])]
    cargo = settings.cargo_actions if with_cargo is None else with_cargo

    nodes, edges = [], []
    last = len(points) - 1
    for i, p in enumerate(points):
        if i < last:
            theta = p.heading_to(points[i + 1])
        else:
            theta = points[i - 1].heading_to(p)
        if i == 0 and start_theta is not None:
            theta = start_theta
        node_id = start_node_id if i == 0 else ('destination' if i == last else f'w{i}')
        node = {
            'nodeId': node_id,
            'sequenceId': 2 * i,
            'released': True,
            'nodePosition': {
                'x': round(p.x, 3), 'y': round(p.y, 3), 'theta': round(theta, 4),
                'mapId': datum.map_id,
                'allowedDeviationXY': (settings.destination_tolerance_m if i == last
                                       else settings.waypoint_tolerance_m),
                # Heading is guidance only: the robot may arrive at any orientation.
                'allowedDeviationTheta': round(math.pi, 4),
            },
            'actions': [],
        }
        if i == last and cargo:
            node['actions'] = [
                _action(order_id, 'openCargo', 'HARD', 'unlock the cargo box'),
                _action(order_id, 'confirmPickup', 'HARD', 'wait until the customer took it'),
                _action(order_id, 'closeCargo', 'HARD', 'lock the cargo box'),
            ]
        nodes.append(node)
        if i > 0:
            edges.append({
                'edgeId': f'e{2 * i - 1}',
                'sequenceId': 2 * i - 1,
                'released': True,
                'startNodeId': nodes[i - 1]['nodeId'],
                'endNodeId': node_id,
                'maxSpeed': settings.max_speed,
                'actions': [],
            })
    order = {'orderId': order_id, 'orderUpdateId': 0, 'nodes': nodes, 'edges': edges}
    return BuiltOrder(order=order, waypoints=points, destination_node_id=nodes[-1]['nodeId'],
                      distance_m=polyline_length(points))


def instant_action(action_type: str, action_id: str, parameters: Optional[Dict] = None) -> Dict:
    """An instantActions body (no header) with one action (cancelOrder, startPause, ...)."""
    return {'actions': [{
        'actionType': action_type, 'actionId': action_id, 'blockingType': 'HARD',
        'actionParameters': [{'key': k, 'value': v} for k, v in (parameters or {}).items()],
    }]}


def with_header(body: Dict, manufacturer: str, serial_number: str, header_id: int,
                timestamp: str) -> Dict:
    """The full VDA 5050 2.0 message: protocol header + body."""
    return {'headerId': header_id, 'timestamp': timestamp, 'version': '2.0.0',
            'manufacturer': manufacturer, 'serialNumber': serial_number, **body}
