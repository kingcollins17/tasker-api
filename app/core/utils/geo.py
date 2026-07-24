import math
from typing import Any, Optional, Sequence


def calculate_haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    earth_radius_km: float = 6371.0,
) -> float:
    """Calculates the great-circle distance between two points on Earth in kilometers using the Haversine formula."""
    phi1, lambda1 = math.radians(lat1), math.radians(lon1)
    phi2, lambda2 = math.radians(lat2), math.radians(lon2)

    dphi = phi2 - phi1
    dlambda = lambda2 - lambda1

    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return earth_radius_km * c


def calculate_locations_distance(locations: Optional[Sequence[Any]]) -> float:
    """Computes Haversine distance in km between the first two locations in a list/sequence."""
    if not locations or len(locations) < 2:
        return 0.0

    loc1, loc2 = locations[0], locations[1]
    lat1 = getattr(loc1, "latitude", None)
    lon1 = getattr(loc1, "longitude", None)
    lat2 = getattr(loc2, "latitude", None)
    lon2 = getattr(loc2, "longitude", None)

    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0.0

    return calculate_haversine_distance(lat1, lon1, lat2, lon2)
