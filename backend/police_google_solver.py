"""
Police Patrol Google Solver — Adapted from Gis_transportation/google_solver.py
Uses Google Cloud Fleet Routing (Route Optimization) API to solve patrol TSP.
Simplified for patrol use: single vehicle, no capacity, waypoint visitation only.
"""
import os
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

GOOGLE_CLOUD_FLEET_ROUTING_URL = (
    "https://routeoptimization.googleapis.com/v1/projects/{project_id}:optimizeTours"
)
ROUTE_OPTIMIZATION_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _get_access_token() -> Optional[str]:
    """Generate an OAuth2 access token from the Service Account JSON file."""
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests

        sa_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not sa_file:
            print("❌ GOOGLE_SERVICE_ACCOUNT_JSON not set in .env")
            return None

        sa_path = Path(__file__).parent / sa_file
        if not sa_path.exists():
            print(f"❌ Service Account JSON not found: {sa_path}")
            return None

        credentials = service_account.Credentials.from_service_account_file(
            str(sa_path), scopes=[ROUTE_OPTIMIZATION_SCOPE]
        )
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        print(f"✓ OAuth2 token generated for: {credentials.service_account_email}")
        return credentials.token

    except Exception as e:
        print(f"❌ Failed to generate OAuth2 token: {e}")
        return None


async def solve_patrol_google(
    station_lat: float,
    station_lng: float,
    waypoints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Solve patrol route using Google Route Optimization API.
    Single vehicle starts at the police station, visits all waypoints, returns.
    """
    project_id = os.getenv("GOOGLE_PROJECT_ID", "")
    if not project_id:
        return {"success": False, "error": "GOOGLE_PROJECT_ID not set"}

    access_token = _get_access_token()
    if not access_token:
        return {"success": False, "error": "OAuth2 token generation failed"}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    model_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    global_end = model_start + timedelta(hours=12)

    # Each waypoint = one shipment (delivery-only, no weight)
    shipments = []
    for i, wp in enumerate(waypoints):
        shipments.append(
            {
                "deliveries": [
                    {
                        "arrivalLocation": {
                            "latitude": wp["lat"],
                            "longitude": wp["lng"],
                        },
                        "duration": "60s",  # 1 min stop at each point
                    }
                ],
                "label": wp.get("name", f"WP_{i}"),
            }
        )

    # Single patrol vehicle starting/ending at the police station
    vehicles = [
        {
            "startLocation": {"latitude": station_lat, "longitude": station_lng},
            "endLocation": {"latitude": station_lat, "longitude": station_lng},
            "costPerKilometer": 1.0,
            "label": "Patrol_Vehicle",
        }
    ]

    request_payload = {
        "model": {
            "shipments": shipments,
            "vehicles": vehicles,
            "globalStartTime": model_start.isoformat() + "Z",
            "globalEndTime": global_end.isoformat() + "Z",
        }
    }

    url = GOOGLE_CLOUD_FLEET_ROUTING_URL.format(project_id=project_id)

    async with httpx.AsyncClient() as client:
        try:
            print(f"→ Sending to Google Route Optimization ({len(waypoints)} waypoints)...")
            response = await client.post(
                url, json=request_payload, headers=headers, timeout=60
            )

            if response.status_code == 200:
                data = response.json()
                return _parse_patrol_response(data, station_lat, station_lng)
            else:
                error_text = response.text[:500]
                print(f"❌ Google API Error {response.status_code}: {error_text}")
                return {"success": False, "error": f"Google API Error: {error_text}"}

        except Exception as e:
            print(f"❌ Google Request Exception: {e}")
            return {"success": False, "error": str(e)}


def _parse_patrol_response(
    data: Dict, station_lat: float, station_lng: float
) -> Dict[str, Any]:
    """Parse Google response into route geometry for map display."""
    routes = data.get("routes", [])
    if not routes:
        return {"success": False, "error": "No routes in Google response"}

    route = routes[0]
    visits = route.get("visits", [])
    transitions = route.get("transitions", [])
    metrics = route.get("metrics", {})

    # Build ordered waypoint list from visits
    ordered_points = [[station_lat, station_lng]]  # Start at station
    for visit in visits:
        if "shipmentLabel" in visit:
            # We need to extract the location — not directly in visit
            # Use transitions for route polyline if available
            pass

    # Fallback: build from visit order (Google returns them in optimized order)
    # The actual geometry would need Directions API for street-level detail
    # For now, return the optimized waypoint order
    wp_coords = []
    for visit in visits:
        idx = visit.get("shipmentIndex", 0)
        # We'll need the caller to match these back
        wp_coords.append(idx)

    distance_km = float(metrics.get("travelDistanceMeters", 0)) / 1000
    duration_min = 0
    total_dur = metrics.get("totalDuration", "0s")
    if isinstance(total_dur, str) and total_dur.endswith("s"):
        duration_min = float(total_dur[:-1]) / 60

    return {
        "success": True,
        "visit_order": wp_coords,
        "distance_km": distance_km,
        "duration_min": duration_min,
    }
