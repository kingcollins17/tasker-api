import pytest
from app.core.utils.geo import calculate_haversine_distance, calculate_locations_distance
from app.features.tasks.schemas import LocationCreate


def test_calculate_haversine_distance():
    # Distance between Lagos (6.5244, 3.3792) and Abuja (9.0765, 7.3986) is approx 525.9 km
    dist = calculate_haversine_distance(6.5244, 3.3792, 9.0765, 7.3986)
    assert 520.0 < dist < 535.0


def test_calculate_locations_distance():
    loc1 = LocationCreate(latitude=6.5244, longitude=3.3792)
    loc2 = LocationCreate(latitude=9.0765, longitude=7.3986)

    dist = calculate_locations_distance([loc1, loc2])
    assert 520.0 < dist < 535.0


def test_calculate_locations_distance_insufficient_locations():
    loc1 = LocationCreate(latitude=6.5244, longitude=3.3792)

    assert calculate_locations_distance([]) == 0.0
    assert calculate_locations_distance(None) == 0.0
    assert calculate_locations_distance([loc1]) == 0.0
