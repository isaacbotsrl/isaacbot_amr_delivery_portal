import pytest

from delivery_portal.domain.geo import GeoPoint
from delivery_portal.domain.ports import RoutingError
from delivery_portal.domain.tickets import Ticket, TicketStatus
from delivery_portal.infrastructure.geocoding import GeocodingError, NominatimGeocoder
from delivery_portal.infrastructure.http import HttpError
from delivery_portal.infrastructure.routing import (GoogleRoutesRouting, OsrmRouting,
                                                    ValhallaRouting, make_router)
from delivery_portal.infrastructure.site_manager import SiteManagerFleet, StaticFleet
from delivery_portal.infrastructure.sqlite_tickets import SqliteTicketRepository

A, B = GeoPoint(44.4268, 26.1025), GeoPoint(44.43, 26.11)


class Fetch:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if self.error:
            raise self.error
        return self.reply


def test_sqlite_round_trip_and_closed_filter(tmp_path):
    repo = SqliteTicketRepository(str(tmp_path / 'db' / 't.sqlite'))
    t = Ticket.create('Ana', B, 'addr', '+40', 'ring twice', now=100)
    t = t.transition(TicketStatus.ASSIGNED, now=101, robot='r1', route=[A, B], order_id='o')
    repo.save(t)
    assert repo.get(t.id) == t
    old = Ticket.create('Old', B, now=0).transition(TicketStatus.CANCELLED, now=1)
    repo.save(old)
    assert {x.id for x in repo.list(50)} == {t.id}
    assert {x.id for x in repo.list()} == {t.id, old.id}
    assert repo.get('nope') is None


def test_osrm():
    fetch = Fetch({'code': 'Ok', 'routes': [{'distance': 123.4, 'geometry': {
        'coordinates': [[26.1025, 44.4268], [26.11, 44.43]]}}]})
    r = OsrmRouting('http://osrm/', fetch=fetch).route(A, B)
    assert r.points == [A, B] and r.distance_m == 123.4
    assert fetch.calls[0][0].startswith('http://osrm/route/v1/foot/26.1025000,44.4268000;')
    with pytest.raises(RoutingError):
        OsrmRouting(fetch=Fetch({'code': 'NoRoute', 'routes': []})).route(A, B)
    with pytest.raises(RoutingError):
        OsrmRouting(fetch=Fetch(error=HttpError('down'))).route(A, B)


def test_valhalla_polyline6():
    # polyline6 of (44.4268, 26.1025) -> (44.43, 26.11)
    shape = encode([A, B], 6)
    fetch = Fetch({'trip': {'legs': [{'shape': shape}], 'summary': {'length': 0.75}}})
    r = ValhallaRouting('http://v', fetch=fetch).route(A, B)
    assert r.distance_m == 750.0 and r.points[0].lat == pytest.approx(A.lat)
    assert fetch.calls[0][1]['body']['costing'] == 'pedestrian'


def test_google_routes():
    fetch = Fetch({'routes': [{'distanceMeters': 900, 'polyline': {
        'encodedPolyline': encode([A, B], 5)}}]})
    r = GoogleRoutesRouting('KEY', fetch=fetch).route(A, B)
    assert r.distance_m == 900 and r.points[-1].lon == pytest.approx(B.lon)
    kw = fetch.calls[0][1]
    assert kw['headers']['X-Goog-Api-Key'] == 'KEY' and kw['body']['travelMode'] == 'WALK'
    with pytest.raises(ValueError):
        GoogleRoutesRouting('')


def test_make_router():
    assert make_router('straight').route(A, B).points == [A, B]
    with pytest.raises(ValueError):
        make_router('valhalla')
    with pytest.raises(ValueError):
        make_router('teleport')


def test_nominatim_rate_limit_and_parse():
    sleeps = []
    t = [0.0]
    fetch = Fetch([{'display_name': 'Str. X 1', 'lat': '44.43', 'lon': '26.11'},
                   {'display_name': 'broken'}])
    g = NominatimGeocoder('http://n', 'ro', fetch=fetch, clock=lambda: t[0],
                          sleep=sleeps.append)
    res = g.search('Str. X', near=A)
    assert [r.label for r in res] == ['Str. X 1'] and res[0].point == B
    assert 'countrycodes=ro' in fetch.calls[0][0] and 'viewbox=' in fetch.calls[0][0]
    g.search('again')
    assert sleeps and sleeps[0] == pytest.approx(1.0)
    with pytest.raises(GeocodingError):
        NominatimGeocoder(fetch=Fetch(error=HttpError('429'))).search('x')


def test_site_manager_fleet():
    fetch = Fetch({'robots': [
        {'name': 'a1', 'serial_number': 'S1', 'manufacturer': 'IsaacBot'},
        {'name': 'a2', 'serial_number': 'S2', 'manufacturer': 'IsaacBot', 'delivery': True},
        {'name': 'bad'}]})
    robots = SiteManagerFleet('http://sm:8020/', 'pw', ['a1'], fetch=fetch).robots()
    assert [(r.name, r.delivery) for r in robots] == [('a1', True), ('a2', True)]
    assert fetch.calls[0][0] == 'http://sm:8020/api/robots'
    assert fetch.calls[0][1]['basic_password'] == 'pw'
    robots = SiteManagerFleet('http://sm', fetch=fetch).robots()
    assert [r.delivery for r in robots] == [False, True]


def test_static_fleet():
    robots = StaticFleet('sim1:SIM1, sim2:SIM2:Acme, broken').robots()
    assert [(r.name, r.serial_number, r.manufacturer) for r in robots] == [
        ('sim1', 'SIM1', 'IsaacBot'), ('sim2', 'SIM2', 'Acme')]
    assert robots[0].topic_prefix == 'uagv/v2/IsaacBot/SIM1'


def encode(points, precision):
    """Reference polyline encoder (test helper)."""
    out, plat, plon, f = [], 0, 0, 10 ** precision
    for p in points:
        lat, lon = round(p.lat * f), round(p.lon * f)
        for d in (lat - plat, lon - plon):
            v = ~(d << 1) if d < 0 else d << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = lat, lon
    return ''.join(out)
