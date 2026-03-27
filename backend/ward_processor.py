"""
Ward Processor — Extracts Mumbai wards from the India Wards shapefile,
assigns police stations to their real ward polygons, and generates
uniform patrol waypoints inside the ward boundary.
"""
import os
import json
import math
import geopandas as gpd
from shapely.geometry import Point, mapping
import numpy as np

WARD_DATA_DIR = os.path.join(os.path.dirname(__file__), "ward_data", "India_Wards")
SHAPEFILE_PATH = os.path.join(WARD_DATA_DIR, "india_wards.shp")
GEOJSON_CACHE = os.path.join(os.path.dirname(__file__), "mumbai_wards.geojson")

# Mumbai bounding box (approximate) to filter from India-wide data
MUMBAI_BOUNDS = {
    "min_lat": 18.85,
    "max_lat": 19.30,
    "min_lng": 72.75,
    "max_lng": 73.05,
}

_ward_gdf = None  # Module-level cache


def _load_mumbai_wards() -> gpd.GeoDataFrame:
    """
    Load Mumbai ward polygons from the shapefile.
    Filters India-wide wards to only Mumbai district.
    Caches as GeoJSON for faster subsequent loads.
    """
    global _ward_gdf
    if _ward_gdf is not None:
        return _ward_gdf

    # Try cached GeoJSON first
    if os.path.exists(GEOJSON_CACHE):
        print(f"📂 Loading cached Mumbai wards from {GEOJSON_CACHE}")
        _ward_gdf = gpd.read_file(GEOJSON_CACHE)
        # Ensure WGS84
        if _ward_gdf.crs and _ward_gdf.crs != "EPSG:4326":
            _ward_gdf = _ward_gdf.to_crs("EPSG:4326")
        return _ward_gdf

    # Load from shapefile and filter
    print(f"📂 Loading shapefile: {SHAPEFILE_PATH}")
    gdf = gpd.read_file(SHAPEFILE_PATH)

    # Filter to Mumbai district
    mumbai = gdf[gdf["district"].str.contains("Mumbai", case=False, na=False)].copy()
    print(f"   Found {len(mumbai)} Mumbai wards")

    # Reproject to WGS84 (lat/lng) if needed
    if mumbai.crs and mumbai.crs != "EPSG:4326":
        mumbai = mumbai.to_crs("EPSG:4326")

    # Cache as GeoJSON
    mumbai.to_file(GEOJSON_CACHE, driver="GeoJSON")
    print(f"   Cached to {GEOJSON_CACHE}")

    _ward_gdf = mumbai
    return _ward_gdf


def find_ward_for_station(lat: float, lng: float) -> dict:
    """
    Given a police station coordinate, find which ward polygon it falls inside.

    Returns:
        {
            "ward_name": str,
            "ward_id": int,
            "polygon": shapely Polygon (for waypoint generation),
            "geojson": dict (for frontend rendering)
        }
    or None if no ward found.
    """
    gdf = _load_mumbai_wards()
    point = Point(lng, lat)  # Shapely uses (x=lng, y=lat)

    for _, row in gdf.iterrows():
        if row.geometry.contains(point):
            return {
                "ward_name": row.get("name", "Unknown"),
                "ward_id": row.get("id", 0),
                "polygon": row.geometry,
                "geojson": mapping(row.geometry),
            }

    # If exact containment fails, find nearest ward
    distances = gdf.geometry.distance(point)
    nearest_idx = distances.idxmin()
    nearest = gdf.loc[nearest_idx]
    return {
        "ward_name": nearest.get("name", "Unknown"),
        "ward_id": nearest.get("id", 0),
        "polygon": nearest.geometry,
        "geojson": mapping(nearest.geometry),
    }


def generate_ward_waypoints(polygon, spacing_km: float = 0.4) -> list:
    """
    Generate a uniform grid of patrol waypoints INSIDE the ward polygon.

    Algorithm:
    1. Get the polygon's bounding box
    2. Create a grid of points spaced `spacing_km` apart
    3. Keep only points that fall inside the polygon
    4. This guarantees every part of the ward is within ~(spacing/2) km of a waypoint

    Args:
        polygon: Shapely Polygon/MultiPolygon
        spacing_km: Distance between grid points in km (default 0.4 = 400m)

    Returns:
        List of {"lat": float, "lng": float, "name": str}
    """
    bounds = polygon.bounds  # (minx, miny, maxx, maxy) = (min_lng, min_lat, max_lng, max_lat)
    min_lng, min_lat, max_lng, max_lat = bounds

    # Convert km spacing to degrees
    # 1 degree lat ≈ 111 km
    lat_step = spacing_km / 111.0
    # 1 degree lng ≈ 111 * cos(lat) km
    center_lat = (min_lat + max_lat) / 2
    lng_step = spacing_km / (111.0 * math.cos(math.radians(center_lat)))

    waypoints = []
    lat = min_lat
    idx = 0
    while lat <= max_lat:
        lng = min_lng
        while lng <= max_lng:
            pt = Point(lng, lat)
            if polygon.contains(pt):
                idx += 1
                waypoints.append({
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "name": f"Patrol Point {idx}",
                })
            lng += lng_step
        lat += lat_step

    # If ward is too small and no points generated, add the centroid
    if len(waypoints) == 0:
        centroid = polygon.centroid
        waypoints.append({
            "lat": round(centroid.y, 6),
            "lng": round(centroid.x, 6),
            "name": "Patrol Point 1 (centroid)",
        })

    return waypoints


def get_all_ward_geojson() -> list:
    """
    Returns all Mumbai ward polygons as a list of GeoJSON features
    for the frontend to render as overlays.
    """
    gdf = _load_mumbai_wards()
    features = []
    for _, row in gdf.iterrows():
        features.append({
            "type": "Feature",
            "properties": {
                "ward_name": row.get("name", "Unknown"),
                "ward_id": row.get("id", 0),
            },
            "geometry": mapping(row.geometry),
        })
    return features


# Pre-warm on import
if os.path.exists(SHAPEFILE_PATH) or os.path.exists(GEOJSON_CACHE):
    try:
        _load_mumbai_wards()
    except Exception as e:
        print(f"⚠️ Ward data not loaded: {e}")
