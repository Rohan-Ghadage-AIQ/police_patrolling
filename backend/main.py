"""
Mumbai Police Patrolling — FastAPI Backend
Configurable solver cascade: Google → VRP (OR-Tools) → OSRM
Uses authoritative Mumbai shapefiles:
  - Point_Police_stations.shp       (91 station locations)
  - Police_station_jurdition.shp    (91 jurisdiction polygons)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import requests as http_requests
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
import traceback
import math

# Load .env from this directory
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from ward_processor import (
    find_ward_for_station,
    find_jurisdiction_for_station,
    generate_ward_waypoints,
    get_all_ward_geojson,
    get_all_stations,
)

app = FastAPI(title="Mumbai Police Patrolling Routing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _solver_enabled(key: str) -> bool:
    return os.getenv(key, "false").lower() in ("true", "1", "yes")


# ─────────────────────────── API Routes ───────────────────────────


@app.get("/")
def home():
    return {
        "message": "Police Patrolling Active",
        "solvers": {
            "google": _solver_enabled("USE_GOOGLE_OPTIMIZATION"),
            "vrp": _solver_enabled("USE_VRP_SOLVER"),
            "osrm": _solver_enabled("USE_OSRM"),
        },
    }


@app.get("/api/stations")
def get_stations_data():
    """Return all 91 police stations enriched with their own jurisdiction name."""
    try:
        stations = get_all_stations()
        for st in stations:
            ward_info = find_jurisdiction_for_station(st["name"], st["lat"], st["lng"])
            st["ward"] = ward_info["ward_name"] if ward_info else "Unknown"
        return {"status": "success", "data": stations}
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


@app.get("/api/wards")
def get_wards():
    """Return all Mumbai ward polygons as GeoJSON features for map overlay."""
    try:
        features = get_all_ward_geojson()
        return {
            "status": "success",
            "type": "FeatureCollection",
            "features": features,
        }
    except Exception as e:
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


class PatrolRequest(BaseModel):
    station_name: str
    lat: float
    lng: float
    spacing_km: Optional[float] = 0.4


@app.post("/api/generate-patrol-route")
def generate_patrol_route(req: PatrolRequest):
    """
    Generate an optimized patrol route for a police station.

    1. Find the station's own jurisdiction polygon (territory-enforced)
    2. Generate grid waypoints inside that polygon
    3. Run solver cascade: Google → VRP → OSRM → Fallback
    4. Get actual road geometry from OSRM Route API
    """
    # ── Step 1: own jurisdiction polygon ──────────────────────────────
    ward_info = find_jurisdiction_for_station(req.station_name, req.lat, req.lng)
    ward_name = ward_info["ward_name"] if ward_info else "Unknown"
    ward_geojson = ward_info["geojson"] if ward_info else None

    # ── Step 2: waypoints inside polygon ──────────────────────────────
    if ward_info and ward_info.get("polygon"):
        waypoints = generate_ward_waypoints(
            ward_info["polygon"], spacing_km=req.spacing_km
        )
        print(
            f"📍 Generated {len(waypoints)} patrol waypoints inside '{ward_name}' "
            f"(spacing={req.spacing_km}km)"
        )
    else:
        waypoints = []
        for i in range(8):
            angle = (2 * math.pi * i) / 8
            lat = req.lat + (0.01 * math.sin(angle))
            lng = req.lng + (0.01 * math.cos(angle))
            waypoints.append(
                {"lat": lat, "lng": lng, "name": f"Patrol Point {i + 1}"}
            )

    if not waypoints:
        return {"status": "error", "message": "No waypoints generated for this ward"}

    # ── Step 3: Solver cascade ────────────────────────────────────────
    result = None

    # --- Google Route Optimization ---
    if _solver_enabled("USE_GOOGLE_OPTIMIZATION") and result is None:
        print("🚀 Trying GOOGLE Route Optimization...")
        try:
            import asyncio
            from police_google_solver import solve_patrol_google

            loop = asyncio.new_event_loop()
            google_result = loop.run_until_complete(
                solve_patrol_google(req.lat, req.lng, waypoints)
            )
            loop.close()

            if google_result.get("success"):
                visit_order = google_result.get("visit_order", [])
                if visit_order:
                    ordered = [waypoints[i] for i in visit_order if i < len(waypoints)]
                else:
                    ordered = waypoints
                result = _build_route_with_osrm(req, ordered, ward_name, ward_geojson)
                if result:
                    result["solver_used"] = "google"
                    result["distance_km"] = google_result.get(
                        "distance_km", result.get("distance_km", 0)
                    )
                    result["duration_min"] = google_result.get(
                        "duration_min", result.get("duration_min", 0)
                    )
                    print(f"✅ Google solver succeeded")
        except Exception as e:
            print(f"❌ Google solver failed: {e}")
            traceback.print_exc()

    # --- VRP Solver (OR-Tools) ---
    if _solver_enabled("USE_VRP_SOLVER") and result is None:
        print("🚀 Trying VRP Solver (OR-Tools)...")
        try:
            from police_vrp_solver import solve_patrol_vrp

            vrp_result = solve_patrol_vrp(req.lat, req.lng, waypoints)
            if vrp_result.get("success"):
                ordered = vrp_result.get("ordered_waypoints", waypoints)
                result = _build_route_with_osrm(req, ordered, ward_name, ward_geojson)
                if result:
                    result["solver_used"] = "vrp"
                    result["distance_km"] = vrp_result.get("distance_km", 0)
                    result["duration_min"] = vrp_result.get("duration_min", 0)
                    print(f"✅ VRP solver succeeded")
        except Exception as e:
            print(f"❌ VRP solver failed: {e}")
            traceback.print_exc()

    # --- OSRM (default fallback) ---
    if _solver_enabled("USE_OSRM") and result is None:
        print("🚀 Trying OSRM routing...")
        try:
            result = _solve_with_osrm(req, waypoints, ward_name, ward_geojson)
            if result:
                result["solver_used"] = "osrm"
                print(f"✅ OSRM solver succeeded")
        except Exception as e:
            print(f"❌ OSRM solver failed: {e}")
            traceback.print_exc()

    # --- Final fallback: direct lines ---
    if result is None:
        print("⚠️ All solvers failed. Using direct waypoint lines.")
        all_pts = [{"lat": req.lat, "lng": req.lng}] + waypoints
        route_geom = [[p["lat"], p["lng"]] for p in all_pts] + [
            [req.lat, req.lng]
        ]
        result = {
            "status": "success",
            "station": req.station_name,
            "ward": ward_name,
            "ward_geojson": ward_geojson,
            "route_geometry": route_geom,
            "waypoint_count": len(waypoints),
            "solver_used": "fallback",
            "note": "All solvers unavailable. Direct waypoint connections shown.",
        }

    return result


# ─────────────────────── OSRM Helpers ────────────────────────────────────────


def _build_route_with_osrm(req, ordered_waypoints, ward_name, ward_geojson):
    """
    Given an ordered list of waypoints, get actual street geometry from OSRM Route API.
    """
    all_points = [{"lat": req.lat, "lng": req.lng}] + ordered_waypoints + [{"lat": req.lat, "lng": req.lng}]
    coords_str = ";".join([f"{p['lng']},{p['lat']}" for p in all_points])
    osrm_url = f"http://router.project-osrm.org/route/v1/driving/{coords_str}?geometries=geojson&overview=full"

    try:
        resp = http_requests.get(osrm_url, timeout=30)
        data = resp.json()
        if data.get("code") == "Ok":
            route = data["routes"][0]
            coords = route["geometry"]["coordinates"]
            route_geom = [[c[1], c[0]] for c in coords]
            return {
                "status": "success",
                "station": req.station_name,
                "ward": ward_name,
                "ward_geojson": ward_geojson,
                "route_geometry": route_geom,
                "waypoint_count": len(ordered_waypoints),
                "distance_km": round(route["distance"] / 1000, 2),
                "duration_min": round(route["duration"] / 60, 1),
            }
    except Exception as e:
        print(f"  OSRM Route API failed: {e}")
    return None


def _solve_with_osrm(req, waypoints, ward_name, ward_geojson):
    """Use OSRM Trip API (TSP solver) to optimize and get street geometry."""
    all_points = [{"lat": req.lat, "lng": req.lng}] + waypoints
    coords_str = ";".join([f"{p['lng']},{p['lat']}" for p in all_points])
    osrm_url = f"http://router.project-osrm.org/trip/v1/driving/{coords_str}?roundtrip=true&source=first&geometries=geojson"

    try:
        resp = http_requests.get(osrm_url, timeout=30)
        data = resp.json()
        if data.get("code") == "Ok":
            trip = data["trips"][0]
            coords = trip["geometry"]["coordinates"]
            route_geom = [[c[1], c[0]] for c in coords]
            return {
                "status": "success",
                "station": req.station_name,
                "ward": ward_name,
                "ward_geojson": ward_geojson,
                "route_geometry": route_geom,
                "waypoint_count": len(waypoints),
                "distance_km": round(trip["distance"] / 1000, 2),
                "duration_min": round(trip["duration"] / 60, 1),
            }
    except Exception as e:
        print(f"  OSRM Trip API failed: {e}")
    return None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
