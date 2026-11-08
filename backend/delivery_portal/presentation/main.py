"""Wiring: configuration -> adapters -> service -> HTTP app.  `uvicorn delivery_portal.presentation.main:app`"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from ..application.delivery_service import DeliveryService, Settings
from ..domain.eta import EtaEstimator
from ..domain.geo import EnuDatum, GeoPoint
from ..domain.order_builder import OrderSettings
from ..infrastructure.geocoding import DEFAULT_NOMINATIM_URL, NominatimGeocoder
from ..infrastructure.mqtt_bridge import MqttBridge
from ..infrastructure.routing import make_router
from ..infrastructure.site_manager import SiteManagerFleet, StaticFleet
from ..infrastructure.sqlite_tickets import SqliteTicketRepository
from .api import create_app
from .config import Config

FLEET_REFRESH_S = 30.0

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('delivery_portal')


def build(cfg: Config):
    datum = EnuDatum(GeoPoint(cfg.datum_lat, cfg.datum_lon), cfg.enu_map_id)
    shop = GeoPoint(cfg.shop_lat, cfg.shop_lon)
    fleet = (StaticFleet(cfg.static_fleet) if cfg.static_fleet else
             SiteManagerFleet(cfg.site_manager_url, cfg.site_manager_password,
                              cfg.delivery_robots))
    bridge = MqttBridge(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user, cfg.mqtt_password)
    settings = Settings(
        order=OrderSettings(spacing_m=cfg.waypoint_spacing_m, max_speed=cfg.max_speed,
                            destination_tolerance_m=cfg.destination_tolerance_m,
                            cargo_actions=cfg.cargo_actions),
        eta=EtaEstimator(speed=cfg.max_speed), auto_return=cfg.auto_return,
        start_trip=cfg.start_trip)
    service = DeliveryService(
        datum, shop,
        make_router(cfg.routing_provider, cfg.routing_url, cfg.google_api_key),
        NominatimGeocoder(cfg.nominatim_url or DEFAULT_NOMINATIM_URL, cfg.geocode_countries),
        fleet, bridge, SqliteTicketRepository(cfg.database), settings)
    bridge.on_state = service.on_state
    bridge.on_connection = service.on_connection
    info = {
        'datum': [cfg.datum_lat, cfg.datum_lon], 'shop': [cfg.shop_lat, cfg.shop_lon],
        'map_id': cfg.enu_map_id, 'routing': cfg.routing_provider,
        'cargo_actions': cfg.cargo_actions, 'auto_return': cfg.auto_return,
        'start_trip': cfg.start_trip,
        'max_speed': cfg.max_speed, 'waypoint_spacing_m': cfg.waypoint_spacing_m,
    }
    stop = threading.Event()

    def refresh_loop():
        while not stop.is_set():
            service.refresh_fleet()
            stop.wait(FLEET_REFRESH_S)

    @asynccontextmanager
    async def lifespan(_app):
        service.refresh_fleet()
        bridge.start()
        threading.Thread(target=refresh_loop, name='fleet-refresh', daemon=True).start()
        log.info('portal up: shop %s, routing %s, %d robots', shop, cfg.routing_provider,
                 len(service.robots()))
        yield
        stop.set()
        bridge.stop()

    return create_app(service, info, cfg.portal_password, cfg.static_dir,
                      mqtt_status=lambda: {'connected': bridge.connected,
                                           'error': bridge.last_error,
                                           'broker': f'{cfg.mqtt_host}:{cfg.mqtt_port}'},
                      lifespan=lifespan)


app = build(Config.from_env())
