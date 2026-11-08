"""The portal's HTTP API (FastAPI) and the built web UI."""

from __future__ import annotations

import base64
import hmac
import json
import os
from typing import Callable, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..application.delivery_service import DeliveryService, NotFound
from ..domain.geo import GeoPoint
from ..domain.ports import RoutingError
from ..domain.settings import DeliverySettings
from ..domain.tickets import TicketError
from ..infrastructure.geocoding import GeocodingError
from ..infrastructure.http import HttpError
from .serialize import geo, plan_json, robot_json, ticket_json


class NewTicket(BaseModel):
    customer: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address: str = Field('', max_length=300)
    phone: str = Field('', max_length=32)
    notes: str = Field('', max_length=1000)


class RobotChoice(BaseModel):
    robot: str = Field(min_length=1, max_length=64)


class Reason(BaseModel):
    reason: str = Field('', max_length=300)


class SettingsBody(BaseModel):
    datum: List[float] = Field(min_length=2, max_length=2)
    shop: List[float] = Field(min_length=2, max_length=2)
    enu_map_id: str = Field('enu_pizzeria', max_length=64)
    parked_heading_deg: Optional[float] = None
    cargo_actions: bool = False
    auto_return: bool = True


def create_app(service: DeliveryService, info: Dict, password: str = '',
               static_dir: str = '', mqtt_status: Optional[Callable[[], Dict]] = None,
               lifespan=None) -> FastAPI:
    app = FastAPI(title='Isaacbot delivery portal', docs_url='/api/docs',
                  openapi_url='/api/openapi.json', lifespan=lifespan)

    @app.middleware('http')
    async def basic_auth(request: Request, call_next):
        if password and request.url.path != '/api/health':
            header = request.headers.get('authorization', '')
            given = ''
            if header.startswith('Basic '):
                try:
                    given = base64.b64decode(header[6:]).decode().partition(':')[2]
                except (ValueError, UnicodeDecodeError):
                    given = ''
            if not hmac.compare_digest(given.encode(), password.encode()):
                return Response(status_code=401, headers={
                    'WWW-Authenticate': 'Basic realm="Isaacbot delivery", charset="UTF-8"'})
        return await call_next(request)

    @app.exception_handler(TicketError)
    async def ticket_error(_, e: TicketError):
        return JSONResponse({'error': str(e)}, status_code=404 if isinstance(e, NotFound) else 400)

    @app.exception_handler(RoutingError)
    async def routing_error(_, e: RoutingError):
        return JSONResponse({'error': f'routing failed: {e}'}, status_code=502)

    @app.exception_handler(GeocodingError)
    async def geocoding_error(_, e: GeocodingError):
        return JSONResponse({'error': str(e)}, status_code=502)

    @app.exception_handler(ConnectionError)
    async def connection_error(_, e: ConnectionError):
        return JSONResponse({'error': str(e)}, status_code=503)

    @app.exception_handler(HttpError)
    async def http_error(_, e: HttpError):
        return JSONResponse({'error': str(e)}, status_code=502)

    @app.get('/api/health')
    def health():
        return {'ok': True}

    @app.get('/api/config')
    def config():
        # Static deployment facts + the editable delivery settings (Settings page).
        return {**info, **service.delivery_settings().to_dict(),
                'map_id': service.datum.map_id}

    @app.get('/api/settings')
    def get_settings():
        return service.delivery_settings().to_dict()

    @app.put('/api/settings')
    def put_settings(body: SettingsBody):
        new = DeliverySettings.from_dict(body.model_dump())
        return service.update_settings(new).to_dict()

    @app.get('/api/snapshot')
    def snapshot():
        return {
            'robots': [robot_json(r) for r in service.robots()],
            'tickets': [ticket_json(t, service) for t in service.list_tickets()],
            'mqtt': mqtt_status() if mqtt_status else {'connected': False, 'error': 'disabled'},
            'fleet_error': service.fleet_error,
        }

    @app.get('/api/geocode')
    def geocode(q: str):
        return [{'label': r.label, 'point': geo(r.point)} for r in service.geocode(q)]

    @app.post('/api/tickets')
    def create_ticket(body: NewTicket):
        t = service.create_ticket(body.customer, GeoPoint(body.lat, body.lon), body.address,
                                  body.phone, body.notes)
        return ticket_json(t, service)

    @app.get('/api/tickets/{ticket_id}')
    def get_ticket(ticket_id: str):
        return ticket_json(service.ticket(ticket_id), service)

    @app.delete('/api/tickets/{ticket_id}')
    def delete_ticket(ticket_id: str):
        service.delete_ticket(ticket_id)
        return {'deleted': ticket_id}

    @app.post('/api/tickets/clear-finished')
    def clear_finished():
        return {'deleted': service.clear_finished()}

    @app.post('/api/tickets/{ticket_id}/plan')
    def plan(ticket_id: str, body: RobotChoice):
        return plan_json(service.plan(ticket_id, body.robot))

    @app.get('/api/tickets/{ticket_id}/order.json')
    def order_json(ticket_id: str, robot: str):
        order = service.plan(ticket_id, robot).order_preview
        return Response(json.dumps(order, indent=2), media_type='application/json', headers={
            'Content-Disposition': f'attachment; filename="{order["orderId"]}.json"'})

    @app.post('/api/tickets/{ticket_id}/assign')
    def assign(ticket_id: str, body: RobotChoice):
        return ticket_json(service.assign(ticket_id, body.robot), service)

    @app.post('/api/tickets/{ticket_id}/delivered')
    def delivered(ticket_id: str):
        return ticket_json(service.mark_delivered(ticket_id), service)

    @app.post('/api/tickets/{ticket_id}/return')
    def send_back(ticket_id: str):
        return ticket_json(service.send_back(ticket_id), service)

    @app.post('/api/tickets/{ticket_id}/cancel')
    def cancel(ticket_id: str, body: Reason):
        return ticket_json(service.cancel(ticket_id, body.reason or 'cancelled by staff'),
                           service)

    @app.post('/api/tickets/{ticket_id}/fail')
    def fail(ticket_id: str, body: Reason):
        return ticket_json(service.fail(ticket_id, body.reason or 'failed'), service)

    @app.post('/api/tickets/{ticket_id}/retry')
    def retry(ticket_id: str):
        return ticket_json(service.retry(ticket_id), service)

    @app.post('/api/robots/{name}/pause')
    def pause(name: str):
        service.pause(name)
        return {'ok': True}

    @app.post('/api/robots/{name}/resume')
    def resume(name: str):
        service.resume(name)
        return {'ok': True}

    @app.post('/api/robots/{name}/return')
    def return_robot(name: str):
        return {'order': service.return_robot(name)}

    @app.post('/api/fleet/refresh')
    def refresh():
        service.refresh_fleet()
        return {'error': service.fleet_error}

    if static_dir and os.path.isdir(static_dir):
        app.mount('/', StaticFiles(directory=static_dir, html=True), name='ui')
    return app
