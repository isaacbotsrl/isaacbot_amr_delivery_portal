"""Domain objects -> JSON for the web UI."""

from __future__ import annotations

from typing import Dict, Optional

from ..application.delivery_service import DeliveryService, Plan
from ..domain.geo import GeoPoint
from ..domain.robots import Robot
from ..domain.tickets import Ticket
from ..infrastructure.sqlite_tickets import ticket_to_dict


def geo(p: Optional[GeoPoint]):
    return [round(p.lat, 7), round(p.lon, 7)] if p is not None else None


def robot_json(r: Robot) -> Dict:
    s = r.status
    return {
        'name': r.name, 'serial_number': r.identity.serial_number,
        'manufacturer': r.identity.manufacturer, 'delivery': r.identity.delivery,
        'connection': s.connection or 'UNKNOWN', 'online': r.online,
        'received_at': s.received_at or None,
        'position': geo(s.position), 'theta': s.theta, 'localization_score': s.localization_score,
        'gps': geo(s.gps), 'gps_fix_type': s.gps_fix_type, 'gps_hdop': s.gps_hdop,
        'map_id': s.map_id, 'battery': s.battery, 'charging': s.charging,
        'operating_mode': s.operating_mode, 'e_stop': s.e_stop,
        'field_violation': s.field_violation, 'driving': s.driving, 'paused': s.paused,
        'order_id': s.order_id, 'last_node_id': s.last_node_id,
        'remaining_nodes': len(s.remaining_node_ids),
        'actions': [{'id': a.action_id, 'type': a.action_type, 'status': a.status}
                    for a in s.actions],
        'errors': s.errors,
        'trail': [geo(p) for p in r.trail[-300:]],
    }


def ticket_json(t: Ticket, service: DeliveryService) -> Dict:
    d = ticket_to_dict(t)
    d['remaining_s'] = service.remaining_s(t)
    d['active'] = t.active
    return d


def plan_json(p: Plan) -> Dict:
    return {
        'robot': p.robot, 'start': geo(p.start), 'start_source': p.start_source,
        'provider': p.route.provider, 'route': [geo(x) for x in p.route.points],
        'router_distance_m': p.route.distance_m, 'waypoints': [geo(x) for x in p.waypoints],
        'distance_m': p.built.distance_m, 'eta_s': p.eta_s,
        'nodes': len(p.built.order['nodes']), 'order': p.order_preview,
    }
