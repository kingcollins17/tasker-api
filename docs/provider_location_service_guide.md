# Provider Location Service Guide

## Overview

The **ProviderLocationService** ([app/core/services/provider_location.py](file:///Users/mac/collins/dev/tasker-api/app/core/services/provider_location.py)) is an **Abstract Base Class (`ABC`)** defining the spatial location tracking and candidate search interface for service providers.

It offers two pluggable concrete implementations:
1. **`RedisProviderLocationService`**: High-frequency in-memory spatial index (`GEOADD`, `GEOSEARCH`) for real-time location heartbeats and candidate dispatch queues.
2. **`PostGISProviderLocationService`**: Persistent PostgreSQL database spatial implementation utilizing repository queries (`ST_DWithin`, `ST_Distance`) for fallback or analytical spatial queries.

---

## 1. Class Hierarchy

```text
                     ┌──────────────────────────────────┐
                     │     ProviderLocationService      │
                     │              (ABC)               │
                     └────────────────┬─────────────────┘
                                      │
           ┌──────────────────────────┴──────────────────────────┐
           │                                                     │
           ▼                                                     ▼
┌──────────────────────────────┐                       ┌────────────────────────────────┐
│ RedisProviderLocationService │                       │ PostGISProviderLocationService │
│    (In-Memory Real-Time)     │                       │     (Persistent PostgreSQL)    │
└──────────────────────────────┘                       └────────────────────────────────┘
```

---

## 2. Abstract Interface Methods

### `update_provider_location(ping: ProviderLocationPing, ttl_seconds: int = 300) -> bool`
* Ingests a real-time provider location ping and updates the active spatial index.

### `remove_provider_location(provider_id: str) -> bool`
* Evicts a provider from the active spatial index when toggled offline.

### `get_provider_location(provider_id: str) -> Optional[ProviderLocationPing]`
* Retrieves the current location coordinates and metadata for a provider.

### `search_nearby_providers(latitude, longitude, radius_km, limit=50) -> List[NearbyProviderResult]`
* Queries spatial index for candidate providers within `radius_km` sorted by distance ascending (`ASC`).

### `calculate_haversine_distance(lat1, lon1, lat2, lon2) -> float` *(Shared Concrete Method)*
* Computes straight-line great-circle distance in kilometers:

  $$d = 2R \arcsin\left(\sqrt{\sin^2\left(\frac{\Delta\phi}{2}\right) + \cos(\phi_1)\cos(\phi_2)\sin^2\left(\frac{\Delta\lambda}{2}\right)}\right)$$

---

## 3. Code Integration Examples

### Injecting Default Redis Implementation (FastAPI)

```python
from fastapi import APIRouter, Depends
from app.core.services import ProviderLocationService, ProviderLocationPing, get_provider_location_service

router = APIRouter()

@router.post("/providers/location-ping")
async def handle_location_ping(
    ping: ProviderLocationPing,
    provider_loc_service: ProviderLocationService = Depends(get_provider_location_service)
):
    success = await provider_loc_service.update_provider_location(ping)
    return {"success": success}
```

### Instantiating PostGIS Implementation

```python
from app.core.repository import Repository
from app.core.models.users import UserLocation, ProviderProfile
from app.core.services import PostGISProviderLocationService, ProviderLocationPing

postgis_geo_service = PostGISProviderLocationService(
    location_repo=Repository(UserLocation, session),
    provider_profile_repo=Repository(ProviderProfile, session),
)

# Search nearby providers using PostGIS & Repository queries
results = await postgis_geo_service.search_nearby_providers(
    latitude=6.5244,
    longitude=3.3792,
    radius_km=10.0
)
```
