"""
Address search.

Nominatim (OpenStreetMap) by default. Its usage policy: at most 1 request/s, a real
User-Agent, no bulk geocoding; staff typing addresses is well within that. Point NOMINATIM_URL at
a self-hosted instance for heavier use.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional
from urllib.parse import urlencode

from ..domain.geo import GeoPoint
from ..domain.ports import GeocodeResult, Geocoder
from .http import HttpError, request_json

DEFAULT_NOMINATIM_URL = 'https://nominatim.openstreetmap.org'


class GeocodingError(RuntimeError):
    pass


class NominatimGeocoder(Geocoder):
    def __init__(self, base_url: str = DEFAULT_NOMINATIM_URL, country_codes: str = '',
                 bias_km: float = 10.0, fetch=request_json, clock=time.monotonic,
                 sleep=time.sleep):
        self.base_url, self.country_codes, self.bias_km = base_url.rstrip('/'), country_codes, bias_km
        self.fetch, self.clock, self.sleep = fetch, clock, sleep
        self._lock = threading.Lock()
        self._last = -1e9

    def search(self, query: str, near: Optional[GeoPoint] = None,
               limit: int = 5) -> List[GeocodeResult]:
        params = {'q': query, 'format': 'jsonv2', 'limit': str(limit), 'addressdetails': '0'}
        if self.country_codes:
            params['countrycodes'] = self.country_codes
        if near is not None and self.bias_km > 0:
            d = self.bias_km / 111.0  # degrees, roughly; a bias, not a filter
            params['viewbox'] = f'{near.lon - d},{near.lat + d},{near.lon + d},{near.lat - d}'
        with self._lock:  # 1 request per second, whatever the number of staff typing
            wait = 1.0 - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = self.clock()
        try:
            rows = self.fetch(f'{self.base_url}/search?{urlencode(params)}')
        except HttpError as e:
            raise GeocodingError(f'address search: {e}') from e
        out = []
        for row in rows or []:
            try:
                out.append(GeocodeResult(str(row.get('display_name', '')),
                                         GeoPoint(float(row['lat']), float(row['lon']))))
            except (KeyError, TypeError, ValueError):
                continue
        return out
