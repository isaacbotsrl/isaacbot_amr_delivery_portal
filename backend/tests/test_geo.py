import math

import pytest

from delivery_portal.domain.geo import (EnuDatum, EnuPoint, GeoPoint, decode_polyline,
                                        haversine_m, polyline_length, resample)

DATUM = EnuDatum(GeoPoint(44.4268, 26.1025), 'enu_test')


def test_origin_is_zero():
    p = DATUM.to_enu(DATUM.origin)
    assert abs(p.x) < 1e-6 and abs(p.y) < 1e-6


def test_north_and_east_axes():
    north = DATUM.to_enu(GeoPoint(44.4278, 26.1025))   # +0.001 deg latitude
    east = DATUM.to_enu(GeoPoint(44.4268, 26.1035))    # +0.001 deg longitude
    # Meridian arc at 44.4 deg: ~111.12 m per 0.001 deg; parallel: ~79.6 m.
    assert north.x == pytest.approx(0.0, abs=0.01) and north.y == pytest.approx(111.12, abs=0.2)
    assert east.y == pytest.approx(0.0, abs=0.01) and east.x == pytest.approx(79.65, abs=0.2)


@pytest.mark.parametrize('lat,lon', [(44.4268, 26.1025), (44.43, 26.11), (44.40, 26.05),
                                     (44.45, 26.15)])
def test_round_trip(lat, lon):
    back = DATUM.to_geo(DATUM.to_enu(GeoPoint(lat, lon)))
    assert back.lat == pytest.approx(lat, abs=1e-7) and back.lon == pytest.approx(lon, abs=1e-7)  # ~1 cm


def test_enu_distance_matches_haversine_locally():
    a, b = GeoPoint(44.4268, 26.1025), GeoPoint(44.4301, 26.1102)
    enu = DATUM.to_enu(a).distance_to(DATUM.to_enu(b))
    assert enu == pytest.approx(haversine_m(a, b), rel=3e-3)


def test_invalid_geopoint():
    with pytest.raises(ValueError):
        GeoPoint(91, 0)
    with pytest.raises(ValueError):
        GeoPoint(float('nan'), 0)


def test_resample_spacing_and_ends():
    line = [EnuPoint(0, 0), EnuPoint(100, 0)]
    pts = resample(line, 8.0)
    assert pts[0] == line[0] and pts[-1] == line[-1]
    gaps = [a.distance_to(b) for a, b in zip(pts, pts[1:])]
    assert all(g <= 8.0 + 1e-9 for g in gaps) and min(gaps) >= 1.0
    assert polyline_length(pts) == pytest.approx(100.0)


def test_resample_keeps_corners():
    pts = resample([EnuPoint(0, 0), EnuPoint(20, 0), EnuPoint(20, 20)], 8.0)
    assert EnuPoint(20, 0) in pts


def test_resample_skips_shallow_bends_and_duplicates():
    pts = resample([EnuPoint(0, 0), EnuPoint(0, 0), EnuPoint(10, 1), EnuPoint(20, 0)], 50.0)
    assert pts == [EnuPoint(0, 0), EnuPoint(20, 0)]


def test_resample_end_wins_when_crowded():
    pts = resample([EnuPoint(0, 0), EnuPoint(16.5, 0)], 8.0)
    assert pts[-1] == EnuPoint(16.5, 0)
    assert all(a.distance_to(b) >= 1.0 for a, b in zip(pts, pts[1:]))


def test_heading_is_enu():
    assert EnuPoint(0, 0).heading_to(EnuPoint(0, 5)) == pytest.approx(math.pi / 2)


def test_decode_polyline_google_example():
    pts = decode_polyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    assert [(round(p.lat, 3), round(p.lon, 3)) for p in pts] == [
        (38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
