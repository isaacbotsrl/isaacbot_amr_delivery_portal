"""In-memory adapters for the service tests."""

from delivery_portal.domain.geo import GeoPoint, haversine_m
from delivery_portal.domain.ports import (FleetSource, OrderPublisher, RoutedPath,
                                          RoutingError, RoutingProvider)
from delivery_portal.domain.robots import RobotIdentity

SHOP = GeoPoint(44.4268, 26.1025)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class LRouter(RoutingProvider):
    """An L-shaped walk: north first, then east (or fails when told to)."""

    def __init__(self):
        self.fail = False
        self.calls = []

    def route(self, start, end):
        self.calls.append((start, end))
        if self.fail:
            raise RoutingError('router down')
        corner = GeoPoint(end.lat, start.lon)
        pts = [start, corner, end]
        return RoutedPath(pts, haversine_m(start, corner) + haversine_m(corner, end), 'fake')


class Fleet(FleetSource):
    def __init__(self, robots=None):
        self.list = robots if robots is not None else [
            RobotIdentity('ardennes_1', 'SN1'), RobotIdentity('rmf_bot', 'SN9', delivery=False)]
        self.fail = False

    def robots(self):
        if self.fail:
            raise OSError('site manager down')
        return list(self.list)


class Publisher(OrderPublisher):
    def __init__(self):
        self.orders, self.instant = [], []
        self.down = False

    def publish_order(self, robot, order):
        if self.down:
            raise ConnectionError('broker down')
        self.orders.append((robot.name, order))

    def publish_instant_actions(self, robot, actions):
        self.instant.append((robot.name, actions))


def state(x=0.0, y=0.0, map_id='enu_test', order_id='', last_node='', last_seq=0,
          remaining=(), driving=False, actions=(), errors=(), gps=None, e_stop='NONE',
          initialized=True):
    s = {
        'headerId': 1, 'orderId': order_id, 'orderUpdateId': 0, 'lastNodeId': last_node,
        'lastNodeSequenceId': last_seq, 'driving': driving, 'paused': False,
        'operatingMode': 'AUTOMATIC',
        'nodeStates': [{'nodeId': n, 'sequenceId': 0, 'released': True} for n in remaining],
        'edgeStates': [],
        'agvPosition': {'x': x, 'y': y, 'theta': 0.0, 'mapId': map_id,
                        'positionInitialized': initialized, 'localizationScore': 0.9},
        'batteryState': {'batteryCharge': 80.0, 'charging': False},
        'safetyState': {'eStop': e_stop, 'fieldViolation': False},
        'actionStates': [{'actionId': a, 'actionType': a.rsplit('-', 1)[-1], 'actionStatus': st}
                         for a, st in actions],
        'errors': [{'errorType': t, 'errorLevel': lvl, 'errorDescription': d}
                   for t, lvl, d in errors],
        'information': [],
    }
    if gps:
        s['information'].append({'infoType': 'gpsFix', 'infoLevel': 'INFO', 'infoReferences': [
            {'referenceKey': 'lat', 'referenceValue': str(gps[0])},
            {'referenceKey': 'lon', 'referenceValue': str(gps[1])},
            {'referenceKey': 'fixType', 'referenceValue': 'GPS'},
            {'referenceKey': 'hdop', 'referenceValue': '0.9'}]})
    return s
