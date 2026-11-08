"""HTTP API tests (need fastapi + httpx: run in the Docker test stage)."""

import base64

import pytest

from delivery_portal.application.delivery_service import DeliveryService
from delivery_portal.domain.geo import EnuDatum, EnuPoint
from delivery_portal.infrastructure.sqlite_tickets import SqliteTicketRepository

from .fakes import SHOP, Fleet, LRouter, Publisher, state

try:  # (a module-level importorskip trips the ROS pytest plugins on dev machines)
    from fastapi.testclient import TestClient

    from delivery_portal.presentation.api import create_app
    HAVE_FASTAPI = True
except ImportError:
    HAVE_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAVE_FASTAPI, reason='fastapi/httpx not installed')

DATUM = EnuDatum(SHOP, 'enu_test')


def auth(pw='secret'):
    return {'Authorization': 'Basic ' + base64.b64encode(f'staff:{pw}'.encode()).decode()}


@pytest.fixture
def client():
    svc = DeliveryService(DATUM, SHOP, LRouter(), None, Fleet(), Publisher(),
                          SqliteTicketRepository(':memory:'))
    svc.refresh_fleet()
    svc.on_state('IsaacBot', 'SN1', state())
    return TestClient(create_app(svc, {'shop': [SHOP.lat, SHOP.lon]}, password='secret'))


def test_auth(client):
    assert client.get('/api/snapshot').status_code == 401
    assert client.get('/api/snapshot', headers=auth('nope')).status_code == 401
    assert client.get('/api/health').status_code == 200


def test_ticket_flow(client):
    h = auth()
    dest = DATUM.to_geo(EnuPoint(40, 30))
    r = client.post('/api/tickets', headers=h, json={'customer': 'Ana', 'lat': dest.lat,
                                                     'lon': dest.lon, 'address': 'X 1'})
    assert r.status_code == 200, r.text
    tid = r.json()['id']
    plan = client.post(f'/api/tickets/{tid}/plan', headers=h, json={'robot': 'ardennes_1'})
    assert plan.status_code == 200 and plan.json()['order']['version'] == '2.0.0'
    dl = client.get(f'/api/tickets/{tid}/order.json', headers=h, params={'robot': 'ardennes_1'})
    assert 'attachment' in dl.headers['content-disposition']
    r = client.post(f'/api/tickets/{tid}/assign', headers=h, json={'robot': 'ardennes_1'})
    assert r.json()['status'] == 'ASSIGNED'
    snap = client.get('/api/snapshot', headers=h).json()
    assert snap['robots'][0]['name'] == 'ardennes_1' and snap['tickets'][0]['id'] == tid
    assert client.post(f'/api/tickets/{tid}/delivered', headers=h).status_code == 400
    assert client.get('/api/tickets/missing', headers=h).status_code == 404
    assert client.post('/api/tickets', headers=h, json={'customer': '', 'lat': 0,
                                                        'lon': 0}).status_code == 422
    r = client.post(f'/api/tickets/{tid}/cancel', headers=h, json={'reason': 'customer called'})
    assert r.json()['status'] == 'CANCELLED'
