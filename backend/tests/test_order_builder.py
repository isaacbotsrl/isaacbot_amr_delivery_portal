import math

import pytest

from delivery_portal.domain.eta import EtaEstimator
from delivery_portal.domain.geo import EnuDatum, EnuPoint, GeoPoint
from delivery_portal.domain.order_builder import (OrderSettings, build_order, instant_action,
                                                  with_header)

DATUM = EnuDatum(GeoPoint(44.4268, 26.1025), 'enu_test')


def route_east(metres):
    end = DATUM.to_geo(EnuPoint(metres, 0))
    return [DATUM.origin, end]


def test_order_structure():
    built = build_order('o1', DATUM, EnuPoint(0, 0), route_east(50), OrderSettings(spacing_m=8))
    nodes, edges = built.order['nodes'], built.order['edges']
    assert nodes[0]['nodeId'] == 'start' and nodes[-1]['nodeId'] == 'destination'
    assert [n['sequenceId'] for n in nodes] == list(range(0, 2 * len(nodes), 2))
    assert [e['sequenceId'] for e in edges] == list(range(1, 2 * len(nodes) - 1, 2))
    for e, a, b in zip(edges, nodes, nodes[1:]):
        assert (e['startNodeId'], e['endNodeId']) == (a['nodeId'], b['nodeId'])
        assert e['maxSpeed'] == 0.8 and e['released']
    assert all(n['nodePosition']['mapId'] == 'enu_test' for n in nodes)
    assert all(n['nodePosition']['theta'] == pytest.approx(0.0, abs=1e-3) for n in nodes)
    assert nodes[-1]['nodePosition']['x'] == pytest.approx(50.0, abs=0.01)
    assert nodes[-1]['nodePosition']['allowedDeviationXY'] == 3.0
    assert nodes[1]['nodePosition']['allowedDeviationXY'] == 2.0
    assert built.distance_m == pytest.approx(50.0, abs=0.05)
    assert nodes[-1]['actions'] == []


def test_cargo_actions_on_destination():
    built = build_order('o2', DATUM, EnuPoint(0, 0), route_east(20),
                        OrderSettings(cargo_actions=True))
    actions = built.order['nodes'][-1]['actions']
    assert [a['actionType'] for a in actions] == ['openCargo', 'confirmPickup', 'closeCargo']
    assert actions[1]['actionId'] == 'o2-confirmPickup'
    assert all(not n['actions'] for n in built.order['nodes'][:-1])


def test_start_is_robot_position_and_theta():
    built = build_order('o3', DATUM, EnuPoint(-5, 0), route_east(30), start_theta=1.0)
    first = built.order['nodes'][0]['nodePosition']
    assert first['x'] == -5 and first['theta'] == 1.0


def test_already_there_is_still_two_nodes():
    built = build_order('o4', DATUM, EnuPoint(0, 0), [DATUM.origin])
    assert len(built.order['nodes']) == 2


def test_rejects_empty():
    with pytest.raises(ValueError):
        build_order('o', DATUM, EnuPoint(0, 0), [])


def test_header_and_instant_action():
    msg = with_header({'orderId': 'x'}, 'IsaacBot', 'SN1', 7, 'T')
    assert msg['version'] == '2.0.0' and msg['serialNumber'] == 'SN1' and msg['orderId'] == 'x'
    ia = instant_action('cancelOrder', 'c1')
    assert ia['actions'][0]['actionType'] == 'cancelOrder'


def test_eta():
    eta = EtaEstimator(speed=0.8, efficiency=0.8, handoff_s=60)
    assert eta.trip_s(640) == pytest.approx(1000)
    assert eta.round_trip_s(640) == pytest.approx(2060)
    pts = [EnuPoint(0, 0), EnuPoint(64, 0), EnuPoint(128, 0)]
    assert eta.remaining_s(pts, 0) == pytest.approx(200)
    assert eta.remaining_s(pts, 1, EnuPoint(96, 0)) == pytest.approx(50)
    assert eta.remaining_s(pts, 2) == 0
    with pytest.raises(ValueError):
        EtaEstimator(speed=0)
