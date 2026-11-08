"""Portal configuration from the environment (.env in docker compose)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, '')
    try:
        return float(raw) if raw.strip() else default
    except ValueError:
        raise ValueError(f'{name}={raw!r} is not a number') from None


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, '').strip().lower()
    return default if not raw else raw in ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class Config:
    datum_lat: float
    datum_lon: float
    enu_map_id: str = 'enu_site'
    shop_lat: float = 0.0
    shop_lon: float = 0.0
    portal_password: str = ''
    site_manager_url: str = 'http://10.8.0.1:8020'
    site_manager_password: str = ''
    delivery_robots: List[str] = field(default_factory=list)
    static_fleet: str = ''                 # FLEET=name:serial[:manufacturer],... (no site manager)
    mqtt_host: str = '10.8.0.1'
    mqtt_port: int = 1883
    mqtt_user: str = ''
    mqtt_password: str = ''
    routing_provider: str = 'osrm'
    routing_url: str = ''
    google_api_key: str = ''
    nominatim_url: str = ''
    geocode_countries: str = ''
    database: str = '/data/tickets.sqlite'
    static_dir: str = ''
    waypoint_spacing_m: float = 8.0
    max_speed: float = 0.8
    destination_tolerance_m: float = 3.0
    cargo_actions: bool = False
    auto_return: bool = True
    start_trip: bool = True
    parked_heading_deg: Optional[float] = None

    @staticmethod
    def from_env() -> 'Config':
        # Only the first start's seed: afterwards the Settings page (database) rules.
        lat, lon = _float('DATUM_LAT', 44.4268), _float('DATUM_LON', 26.1025)
        return Config(
            datum_lat=lat, datum_lon=lon,
            enu_map_id=os.environ.get('ENU_MAP_ID', 'enu_site'),
            shop_lat=_float('SHOP_LAT', lat), shop_lon=_float('SHOP_LON', lon),
            portal_password=os.environ.get('PORTAL_PASSWORD', ''),
            site_manager_url=os.environ.get('SITE_MANAGER_URL', 'http://10.8.0.1:8020'),
            site_manager_password=os.environ.get('SITE_MANAGER_PASSWORD', ''),
            delivery_robots=[n for n in os.environ.get('DELIVERY_ROBOTS', '').split(',') if n],
            static_fleet=os.environ.get('FLEET', ''),
            mqtt_host=os.environ.get('MQTT_HOST', '10.8.0.1'),
            mqtt_port=int(_float('MQTT_PORT', 1883)),
            mqtt_user=os.environ.get('MQTT_USER', ''),
            mqtt_password=os.environ.get('MQTT_PASSWORD', ''),
            routing_provider=os.environ.get('ROUTING_PROVIDER', 'osrm'),
            routing_url=os.environ.get('ROUTING_URL', ''),
            google_api_key=os.environ.get('GOOGLE_API_KEY', ''),
            nominatim_url=os.environ.get('NOMINATIM_URL', ''),
            geocode_countries=os.environ.get('GEOCODE_COUNTRIES', ''),
            database=os.environ.get('DATABASE', '/data/tickets.sqlite'),
            static_dir=os.environ.get('STATIC_DIR', ''),
            waypoint_spacing_m=_float('WAYPOINT_SPACING_M', 8.0),
            max_speed=_float('MAX_SPEED', 0.8),
            destination_tolerance_m=_float('DESTINATION_TOLERANCE_M', 3.0),
            cargo_actions=_bool('CARGO_ACTIONS', False),
            auto_return=_bool('AUTO_RETURN', True),
            start_trip=_bool('START_TRIP', True),
            parked_heading_deg=(_float('PARKED_HEADING_DEG', 0.0)
                                if os.environ.get('PARKED_HEADING_DEG', '').strip() else None),
        )
