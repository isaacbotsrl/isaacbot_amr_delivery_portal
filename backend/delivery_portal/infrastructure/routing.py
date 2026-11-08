"""
Pedestrian routing adapters.

- osrm:     OSRM with a foot profile. Default URL: the FOSSGIS public foot router (light use
            only); self-host osrm-backend with your region's OSM extract for production.
- valhalla: Valhalla, `pedestrian` costing.
- google:   Google Routes API, travelMode WALK (needs an API key; billed per request).
- straight: a straight line (simulation and tests; no network).

Traffic is irrelevant to a sidewalk robot: the ETA comes from the walking distance at the
robot's speed (domain/eta.py), not from the router's duration.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional
from urllib.parse import quote

from ..domain.geo import GeoPoint, decode_polyline, haversine_m
from ..domain.ports import RoutedPath, RoutingError, RoutingProvider
from .http import HttpError, request_json

Fetch = Callable[..., Dict]

DEFAULT_OSRM_URL = 'https://routing.openstreetmap.de/routed-foot'


class StraightLineRouting(RoutingProvider):
    def route(self, start: GeoPoint, end: GeoPoint) -> RoutedPath:
        return RoutedPath([start, end], haversine_m(start, end), 'straight')


class OsrmRouting(RoutingProvider):
    def __init__(self, base_url: str = DEFAULT_OSRM_URL, profile: str = 'foot',
                 fetch: Fetch = request_json):
        self.base_url, self.profile, self.fetch = base_url.rstrip('/'), profile, fetch

    def route(self, start: GeoPoint, end: GeoPoint) -> RoutedPath:
        coords = f'{start.lon:.7f},{start.lat:.7f};{end.lon:.7f},{end.lat:.7f}'
        url = (f'{self.base_url}/route/v1/{quote(self.profile)}/{coords}'
               '?overview=full&geometries=geojson&steps=false')
        try:
            data = self.fetch(url)
        except HttpError as e:
            raise RoutingError(f'OSRM: {e}') from e
        if data.get('code') != 'Ok' or not data.get('routes'):
            raise RoutingError(f"OSRM: {data.get('code')} {data.get('message', '')}".strip())
        r = data['routes'][0]
        points = [GeoPoint(lat, lon) for lon, lat in r['geometry']['coordinates']]
        return RoutedPath(points, float(r.get('distance', 0.0)), 'osrm')


class ValhallaRouting(RoutingProvider):
    def __init__(self, base_url: str, fetch: Fetch = request_json):
        self.base_url, self.fetch = base_url.rstrip('/'), fetch

    def route(self, start: GeoPoint, end: GeoPoint) -> RoutedPath:
        body = {'locations': [{'lat': start.lat, 'lon': start.lon},
                              {'lat': end.lat, 'lon': end.lon}],
                'costing': 'pedestrian', 'units': 'kilometers',
                'directions_options': {'directions_type': 'none'}}
        try:
            data = self.fetch(f'{self.base_url}/route', method='POST', body=body)
        except HttpError as e:
            raise RoutingError(f'Valhalla: {e}') from e
        trip = data.get('trip') or {}
        legs = trip.get('legs') or []
        if not legs:
            raise RoutingError(f"Valhalla: no route ({data.get('error', 'unknown')})")
        points = []
        for leg in legs:
            pts = decode_polyline(leg['shape'], precision=6)
            points += pts if not points else pts[1:]
        km = float((trip.get('summary') or {}).get('length', 0.0))
        return RoutedPath(points, km * 1000.0, 'valhalla')


class GoogleRoutesRouting(RoutingProvider):
    URL = 'https://routes.googleapis.com/directions/v2:computeRoutes'

    def __init__(self, api_key: str, fetch: Fetch = request_json):
        if not api_key:
            raise ValueError('GOOGLE_API_KEY is required for ROUTING_PROVIDER=google')
        self.api_key, self.fetch = api_key, fetch

    def route(self, start: GeoPoint, end: GeoPoint) -> RoutedPath:
        def loc(p: GeoPoint) -> Dict:
            return {'location': {'latLng': {'latitude': p.lat, 'longitude': p.lon}}}
        body = {'origin': loc(start), 'destination': loc(end), 'travelMode': 'WALK',
                'polylineEncoding': 'ENCODED_POLYLINE'}
        headers = {'X-Goog-Api-Key': self.api_key,
                   'X-Goog-FieldMask': 'routes.distanceMeters,routes.polyline.encodedPolyline'}
        try:
            data = self.fetch(self.URL, method='POST', body=body, headers=headers)
        except HttpError as e:
            raise RoutingError(f'Google Routes: {e}') from e
        routes = data.get('routes') or []
        if not routes:
            raise RoutingError('Google Routes: no walking route')
        r = routes[0]
        points = decode_polyline(r['polyline']['encodedPolyline'], precision=5)
        return RoutedPath(points, float(r.get('distanceMeters', 0.0)), 'google')


def make_router(provider: str, url: str = '', google_api_key: str = '',
                fetch: Optional[Fetch] = None) -> RoutingProvider:
    provider = (provider or 'osrm').lower()
    kw = {'fetch': fetch} if fetch else {}
    if provider == 'osrm':
        return OsrmRouting(url or DEFAULT_OSRM_URL, **kw)
    if provider == 'valhalla':
        if not url:
            raise ValueError('ROUTING_URL is required for ROUTING_PROVIDER=valhalla')
        return ValhallaRouting(url, **kw)
    if provider == 'google':
        return GoogleRoutesRouting(google_api_key, **kw)
    if provider == 'straight':
        return StraightLineRouting()
    raise ValueError(f'unknown ROUTING_PROVIDER {provider!r} (osrm, valhalla, google, straight)')
