import pytest

from delivery_portal.application.delivery_service import (DeliveryService, NotFound, Settings,
                                                          STALE_S)
from delivery_portal.domain.geo import EnuDatum, EnuPoint, GeoPoint
from delivery_portal.domain.order_builder import OrderSettings
from delivery_portal.domain.ports import RoutingError
from delivery_portal.domain.tickets import TicketError, TicketStatus
from delivery_portal.infrastructure.sqlite_tickets import SqliteTicketRepository

from .fakes import SHOP, Clock, Fleet, LRouter, Publisher, state

DATUM = EnuDatum(SHOP, 'enu_test')
CUSTOMER = DATUM.to_geo(EnuPoint(60, 40))


def make(cargo=False, auto_return=True, start_trip=False):
    clock, router, pub, fleet = Clock(), LRouter(), Publisher(), Fleet()
    svc = DeliveryService(DATUM, SHOP, router, None, fleet, pub, SqliteTicketRepository(':memory:'),
                          Settings(order=OrderSettings(cargo_actions=cargo),
                                   auto_return=auto_return, start_trip=start_trip), clock=clock)
    svc.refresh_fleet()
    return svc, clock, router, pub, fleet


def online(svc, **kw):
    svc.on_state('IsaacBot', 'SN1', state(**kw))


def test_assign_sends_order_from_robot_position():
    svc, _, router, pub, _ = make()
    online(svc, x=2.0, y=1.0)
    t = svc.create_ticket('Ana', CUSTOMER, 'Str. Pizzei 1')
    t = svc.assign(t.id, 'ardennes_1')
    assert t.status == TicketStatus.ASSIGNED and t.robot == 'ardennes_1'
    name, order = pub.orders[-1]
    assert name == 'ardennes_1' and order['orderId'] == t.order_id == t.outbound_order_id
    first = order['nodes'][0]['nodePosition']
    assert (first['x'], first['y']) == (2.0, 1.0)
    assert router.calls[-1][0] == DATUM.to_geo(EnuPoint(2.0, 1.0))
    last = order['nodes'][-1]['nodePosition']
    assert last['x'] == pytest.approx(60, abs=0.01) and last['y'] == pytest.approx(40, abs=0.01)
    assert t.distance_m == pytest.approx(39 + 58, abs=0.5)  # L-shaped walk: north, then east


def test_start_falls_back_to_gps_then_shop():
    svc, _, _, _, _ = make()
    online(svc, map_id='slam_map_1', gps=(CUSTOMER.lat, CUSTOMER.lon))
    t = svc.create_ticket('Ana', CUSTOMER)
    assert svc.plan(t.id, 'ardennes_1').start_source == 'gps'
    online(svc, map_id='slam_map_1')
    assert svc.plan(t.id, 'ardennes_1').start_source == 'shop'


def test_dispatch_rules():
    svc, clock, _, pub, _ = make()
    t = svc.create_ticket('Ana', CUSTOMER)
    with pytest.raises(TicketError, match='offline'):
        svc.assign(t.id, 'ardennes_1')
    online(svc, e_stop='MANUAL')
    with pytest.raises(TicketError, match='emergency'):
        svc.assign(t.id, 'ardennes_1')
    svc.on_state('IsaacBot', 'SN9', state())
    with pytest.raises(TicketError, match='not a delivery robot'):
        svc.assign(t.id, 'rmf_bot')
    with pytest.raises(NotFound):
        svc.assign(t.id, 'ghost')
    online(svc)
    svc.assign(t.id, 'ardennes_1')
    t2 = svc.create_ticket('Bob', CUSTOMER)
    with pytest.raises(TicketError, match='busy'):
        svc.assign(t2.id, 'ardennes_1')
    with pytest.raises(TicketError, match='not NEW'):
        svc.assign(t.id, 'ardennes_1')
    clock.t += STALE_S + 1
    assert svc.robot('ardennes_1').online  # stored state untouched...
    assert not svc.robots()[0].online      # ...but the listing marks it stale


def test_failed_publish_leaves_ticket_new():
    svc, _, _, pub, _ = make()
    online(svc)
    t = svc.create_ticket('Ana', CUSTOMER)
    pub.down = True
    with pytest.raises(ConnectionError):
        svc.assign(t.id, 'ardennes_1')
    assert svc.ticket(t.id).status == TicketStatus.NEW


def test_routing_failure_is_reported():
    svc, _, router, _, _ = make()
    online(svc)
    router.fail = True
    t = svc.create_ticket('Ana', CUSTOMER)
    with pytest.raises(RoutingError):
        svc.assign(t.id, 'ardennes_1')


def test_progress_staff_handoff_and_return():
    svc, _, router, pub, _ = make()
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    oid = t.order_id
    online(svc, order_id=oid, last_node='start', driving=True, remaining=['w1', 'destination'])
    t = svc.ticket(t.id)
    assert t.status == TicketStatus.EN_ROUTE
    assert 0 < svc.remaining_s(t) <= t.eta_s
    online(svc, x=60, y=40, order_id=oid, last_node='destination', last_seq=20)
    assert svc.ticket(t.id).status == TicketStatus.ARRIVED
    t = svc.mark_delivered(t.id)
    assert t.status == TicketStatus.RETURNING and t.return_order_id == t.order_id != oid
    ret = pub.orders[-1][1]
    assert ret['nodes'][-1]['nodeId'] == 'shop' and ret['nodes'][-1]['actions'] == []
    assert router.calls[-1][1] == SHOP
    online(svc, order_id=t.order_id, last_node='shop', remaining=[])
    t = svc.ticket(t.id)
    assert t.status == TicketStatus.DONE
    assert [e.status for e in t.history] == [
        TicketStatus.NEW, TicketStatus.ASSIGNED, TicketStatus.EN_ROUTE, TicketStatus.ARRIVED,
        TicketStatus.DELIVERED, TicketStatus.RETURNING, TicketStatus.DONE]


def test_cargo_confirm_pickup_delivers_and_returns_by_retracing():
    svc, _, router, pub, _ = make(cargo=True)
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    oid = t.order_id
    online(svc, order_id=oid, last_node='destination',
            actions=[(f'{oid}-confirmPickup', 'RUNNING')])
    assert svc.ticket(t.id).status == TicketStatus.ARRIVED
    router.fail = True  # the return falls back to retracing the outbound route
    online(svc, order_id=oid, last_node='destination',
            actions=[(f'{oid}-confirmPickup', 'FINISHED')])
    t = svc.ticket(t.id)
    assert t.status == TicketStatus.RETURNING
    ret = pub.orders[-1][1]
    assert ret['nodes'][-1]['nodeId'] == 'shop'
    assert abs(ret['nodes'][-1]['nodePosition']['x']) < 0.01


def test_no_auto_return_waits_for_staff():
    svc, _, _, pub, _ = make(auto_return=False)
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    online(svc, order_id=t.order_id, last_node='destination')
    t = svc.mark_delivered(t.id)
    assert t.status == TicketStatus.DELIVERED and len(pub.orders) == 1
    assert svc.send_back(t.id).status == TicketStatus.RETURNING


def test_cancel_sends_cancel_order_and_retry():
    svc, _, _, pub, _ = make()
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    t = svc.cancel(t.id)
    assert t.status == TicketStatus.CANCELLED
    assert pub.instant[-1][1]['actions'][0]['actionType'] == 'cancelOrder'
    t = svc.retry(t.id)
    assert t.status == TicketStatus.NEW and t.robot == ''
    t = svc.assign(t.id, 'ardennes_1')
    assert t.order_id.endswith('-2o')


def test_cancel_not_allowed_after_delivery_sends_nothing():
    svc, _, _, pub, _ = make()
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    online(svc, order_id=t.order_id, last_node='destination')
    svc.mark_delivered(t.id)
    with pytest.raises(TicketError):
        svc.cancel(t.id)
    assert pub.instant == []


def test_fatal_error_is_shown_on_ticket():
    svc, _, _, _, _ = make()
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    online(svc, order_id=t.order_id, errors=[('noRouteError', 'FATAL', 'blocked')])
    assert 'blocked' in svc.ticket(t.id).error


def test_other_orders_do_not_move_tickets():
    svc, _, _, _, _ = make()
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    online(svc, order_id='someone-else', last_node='destination')
    assert svc.ticket(t.id).status == TicketStatus.ASSIGNED


def test_fleet_refresh_keeps_state_and_survives_errors():
    svc, _, _, _, fleet = make()
    online(svc, x=1, y=1)
    fleet.fail = True
    svc.refresh_fleet()
    assert 'down' in svc.fleet_error and svc.robot('ardennes_1').status.position_enu
    fleet.fail = False
    svc.refresh_fleet()
    assert svc.fleet_error == '' and svc.robot('ardennes_1').status.position_enu


def test_trail_and_unknown_serial():
    svc, _, _, _, _ = make()
    online(svc, x=1, y=1)
    online(svc, x=1, y=1)
    online(svc, x=2, y=1)
    assert len(svc.robot('ardennes_1').trail) == 2
    svc.on_state('IsaacBot', 'NOPE', state())  # ignored


def test_pause_resume_and_return_robot():
    svc, _, _, pub, _ = make()
    online(svc, x=30, y=0)
    svc.pause('ardennes_1')
    svc.resume('ardennes_1')
    assert [i[1]['actions'][0]['actionType'] for i in pub.instant] == ['startPause', 'stopPause']
    order = svc.return_robot('ardennes_1')
    assert order['nodes'][-1]['nodeId'] == 'shop'


def test_start_trip_first_then_the_order_from_the_new_position():
    svc, _, router, pub, _ = make(start_trip=True)
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    assert t.status == TicketStatus.ASSIGNED and not t.order_sent
    assert pub.orders == []
    trip = pub.instant[-1][1]['actions'][0]
    assert trip['actionType'] == 'startTrip' and trip['actionId'] == t.trip_action_id
    online(svc, actions=[(t.trip_action_id, 'RUNNING')])
    assert pub.orders == []
    # The fresh map put the robot 1 m east of where it was planned from.
    online(svc, x=1.0, y=0.0, actions=[(t.trip_action_id, 'FINISHED')])
    t = svc.ticket(t.id)
    assert t.order_sent and len(pub.orders) == 1
    order = pub.orders[0][1]
    assert order['orderId'] == t.order_id
    assert order['nodes'][0]['nodePosition']['x'] == 1.0
    online(svc, x=1.0, order_id=t.order_id, last_node='start', driving=True)
    assert svc.ticket(t.id).status == TicketStatus.EN_ROUTE


def test_start_trip_failure_fails_the_ticket():
    svc, _, _, pub, _ = make(start_trip=True)
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    online(svc, actions=[(t.trip_action_id, 'FAILED')])
    t = svc.ticket(t.id)
    assert t.status == TicketStatus.FAILED and 'startTrip failed' in t.error
    assert pub.orders == []
    assert svc.retry(t.id).trip_action_id == ''


def test_start_trip_carries_the_pizzeria_frame():
    svc, _, _, pub, _ = make(start_trip=True)
    svc.parked_heading_deg = 90.0
    online(svc)
    svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    params = {p['key']: p['value'] for p in pub.instant[-1][1]['actions'][0]['actionParameters']}
    assert params == {'datum_lat': SHOP.lat, 'datum_lon': SHOP.lon, 'enu_map_id': 'enu_test',
                      'parked_heading_deg': 90.0}


def test_settings_change_moves_the_frame_and_is_refused_during_deliveries():
    from delivery_portal.domain.settings import DeliverySettings
    from delivery_portal.infrastructure.sqlite_tickets import SqliteSettingsRepository
    svc, _, _, pub, _ = make()
    svc.settings_store = store = SqliteSettingsRepository(':memory:')
    new_shop = GeoPoint(44.4300, 26.1100)
    new = DeliverySettings(datum=new_shop, shop=new_shop, enu_map_id='enu_new',
                           parked_heading_deg=180.0, cargo_actions=True, auto_return=False)
    online(svc)
    t = svc.assign(svc.create_ticket('Ana', CUSTOMER).id, 'ardennes_1')
    with pytest.raises(TicketError, match='active'):
        svc.update_settings(new)
    svc.cancel(t.id)
    svc.update_settings(new)
    assert svc.delivery_settings() == new
    assert DeliverySettings.from_dict(store.load()) == new
    assert svc.datum.map_id == new.frame_id and new.frame_id.startswith('enu_new-')
    assert svc.settings.order.cargo_actions
    # Robots still reporting the old frame drop back to GPS / unknown until their next trip.
    online(svc, x=5.0, y=5.0, map_id='enu_test')
    assert svc.robot('ardennes_1').status.position is None


def test_settings_validation():
    from delivery_portal.domain.settings import DeliverySettings
    p = GeoPoint(44.4268, 26.1025)
    with pytest.raises(TicketError, match='compass'):
        DeliverySettings(datum=p, shop=p, parked_heading_deg=400)
    with pytest.raises(TicketError, match='parking spot'):
        DeliverySettings(datum=p, shop=GeoPoint(44.5, 26.1025))
    with pytest.raises(TicketError, match='map id'):
        DeliverySettings(datum=p, shop=p, enu_map_id='bad id/')
    d = DeliverySettings(datum=p, shop=p, parked_heading_deg=None)
    assert DeliverySettings.from_dict(d.to_dict()) == d


def test_frame_id_changes_with_the_location():
    from delivery_portal.domain.settings import DeliverySettings
    a = GeoPoint(44.4268, 26.1025)
    b = GeoPoint(44.4269, 26.1025)
    assert DeliverySettings(datum=a, shop=a).frame_id == DeliverySettings(datum=a, shop=a).frame_id
    assert DeliverySettings(datum=a, shop=a).frame_id != DeliverySettings(datum=b, shop=b).frame_id
