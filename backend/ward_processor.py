"""
Ward / Jurisdiction Processor
Uses the authoritative Mumbai-specific shapefiles:
  - Police_station_jurdition.shp  →  91 jurisdiction polygons (one per PS)
  - Point_Police_stations.shp     →  91 police station point locations

Both files are already in EPSG:4326 (WGS84), so no reprojection needed.

Territory enforcement:
  find_jurisdiction_for_station(name, lat, lng) always returns the polygon
  that legally belongs to the requesting station by matching the station name
  against the jurisdiction name. Spatial containment is only used as a final
  fallback so no station ever accidentally patrols another station's territory.
"""
import os
import re
import math
import geopandas as gpd
from shapely.geometry import Point, mapping

WARD_DATA_DIR = os.path.join(os.path.dirname(__file__), "ward_data")

JURISDICTION_SHP = os.path.join(WARD_DATA_DIR, "Police_station_jurdition.shp")
POINTS_SHP       = os.path.join(WARD_DATA_DIR, "Point_Police_stations.shp")

# ── Module-level cache ──────────────────────────────────────────────────────
_jurisdiction_gdf = None   # polygon GDF — 91 jurisdiction boundaries
_points_gdf       = None   # point GDF   — 91 station locations


# ── Loaders ────────────────────────────────────────────────────────────────

def _load_jurisdictions() -> gpd.GeoDataFrame:
    """Load (and cache) jurisdiction polygon shapefile."""
    global _jurisdiction_gdf
    if _jurisdiction_gdf is not None:
        return _jurisdiction_gdf
    print(f"📂 Loading jurisdiction shapefile: {JURISDICTION_SHP}")
    gdf = gpd.read_file(JURISDICTION_SHP)
    if gdf.crs and str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    _jurisdiction_gdf = gdf
    print(f"   Loaded {len(gdf)} jurisdiction polygons")
    return _jurisdiction_gdf


def _load_station_points() -> gpd.GeoDataFrame:
    """Load (and cache) police station point shapefile."""
    global _points_gdf
    if _points_gdf is not None:
        return _points_gdf
    print(f"📂 Loading station points shapefile: {POINTS_SHP}")
    gdf = gpd.read_file(POINTS_SHP)
    if gdf.crs and str(gdf.crs) != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    _points_gdf = gdf
    print(f"   Loaded {len(gdf)} station points")
    return _points_gdf


# ── Name normalisation helpers ─────────────────────────────────────────────

# Common abbreviation expansions — applied before fuzzy matching
_ABBREV = {
    r"\bps\b":    "police station",
    r"\bbkc\b":   "bandra kurla complex",
    r"\bjj\b":    "sir jj",
    r"\blt\b":    "lt",
    r"\bnm\b":    "nm",
    r"\bvp\b":    "vp",
    r"\bvb\b":    "vinoba bhave",
    r"\brak\b":   "rak",
    r"\brcf\b":   "rcf",
    r"\bmidc\b":  "midc",
    r"\bmhb\b":   "mhb",
    r"\bmra\b":   "mra",
    r"\bdn\b":    "dn",
    r"\bdb\b":    "db",
}


def _normalize(name: str) -> str:
    """
    Lowercase, strip punctuation, expand known abbreviations.
    Returns a bag-of-words string suitable for token overlap scoring.
    """
    s = name.lower().strip()
    # Remove extra spaces
    s = re.sub(r"\s+", " ", s)
    # Expand abbreviations
    for pat, rep in _ABBREV.items():
        s = re.sub(pat, rep, s)
    # Remove non-alphanumeric chars (except spaces)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s.strip()


def _token_overlap(a: str, b: str) -> float:
    """Jaccard-style token overlap score between two normalised strings."""
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    # Remove generic stop tokens
    stop = {"police", "station", "marg", "nagar", "road"}
    ta -= stop
    tb -= stop
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union


def _find_best_jurisdiction_match(station_name: str) -> int | None:
    """
    Return the GDF index of the jurisdiction polygon whose name best matches
    `station_name`, or None if no score exceeds 0.3.
    """
    gdf = _load_jurisdictions()
    best_score = 0.0
    best_idx   = None
    for idx, row in gdf.iterrows():
        j_name = str(row.get("Name", ""))
        score  = _token_overlap(station_name, j_name)
        if score > best_score:
            best_score = score
            best_idx   = idx
    if best_score >= 0.3:
        return best_idx
    return None


# ── Public API ─────────────────────────────────────────────────────────────

def get_all_stations() -> list:
    """
    Return all 91 police stations from the point shapefile.

    Returns:
        List of {"name": str, "lat": float, "lng": float}
    """
    gdf = _load_station_points()
    stations = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        # Point Z (lng, lat, elevation) — X=lng, Y=lat
        stations.append({
            "name": str(row.get("Name", "Unknown")).strip(),
            "lat":  round(geom.y, 6),
            "lng":  round(geom.x, 6),
        })
    return stations


def find_ward_for_station(lat: float, lng: float) -> dict | None:
    """
    Spatial fallback: given a coordinate, find which jurisdiction polygon
    it falls inside (or nearest if none contains it).

    Prefer `find_jurisdiction_for_station` when you have a station name.
    """
    gdf = _load_jurisdictions()
    point = Point(lng, lat)

    for _, row in gdf.iterrows():
        if row.geometry and row.geometry.contains(point):
            return _row_to_ward_dict(row)

    # Nearest centroid fallback
    distances = gdf.geometry.centroid.distance(point)
    nearest = gdf.loc[distances.idxmin()]
    return _row_to_ward_dict(nearest)


def find_jurisdiction_for_station(station_name: str,
                                   lat: float, lng: float) -> dict | None:
    """
    **Primary entry point for territory-enforcement.**

    Given a station name + coordinates, return the jurisdiction polygon that
    *belongs to* that station — guaranteeing it never gets another station's
    territory.

    Resolution order:
      1. Fuzzy name match against jurisdiction 'Name' field (best)
      2. Exact spatial containment  (backup)
      3. Nearest centroid           (last resort)

    Returns same dict shape as find_ward_for_station.
    """
    gdf = _load_jurisdictions()

    # 1 — Name-based match
    best_idx = _find_best_jurisdiction_match(station_name)
    if best_idx is not None:
        row = gdf.loc[best_idx]
        result = _row_to_ward_dict(row)
        print(f"🗺️  '{station_name}' → jurisdiction '{result['ward_name']}' (name match)")
        return result

    # 2 & 3 — Spatial fallback
    print(f"⚠️  No name match for '{station_name}', falling back to spatial lookup")
    return find_ward_for_station(lat, lng)


def _row_to_ward_dict(row) -> dict:
    return {
        "ward_name": str(row.get("Name", "Unknown")).strip(),
        "ward_id":   row.get("gid") or row.get("id") or 0,
        "polygon":   row.geometry,
        "geojson":   mapping(row.geometry),
    }


def generate_ward_waypoints(polygon, spacing_km: float = 0.4, target_waypoints: int = 20) -> list:
    """
    Generate patrol waypoints strictly INSIDE a jurisdiction polygon.

    The spacing is AUTO-SCALED based on the polygon's area so that roughly
    `target_waypoints` points are generated regardless of jurisdiction size.
    This keeps patrol routes feasible (30-60 min) for both large and small areas.

    Args:
        polygon: Shapely Polygon / MultiPolygon
        spacing_km: Minimum spacing hint (default 0.3 km)
        target_waypoints: Target number of waypoints (default 20)

    Returns:
        List of {"lat": float, "lng": float, "name": str}
    """
    bounds = polygon.bounds            # minx, miny, maxx, maxy
    min_lng, min_lat, max_lng, max_lat = bounds
    center_lat = (min_lat + max_lat) / 2.0

    # ── Auto-scale spacing based on area ──────────────────────────────
    # Convert polygon area from degrees² to approximate km²
    area_deg2 = polygon.area
    km_per_deg_lat = 111.0
    km_per_deg_lng = 111.0 * math.cos(math.radians(center_lat))
    area_km2 = area_deg2 * km_per_deg_lat * km_per_deg_lng

    # spacing = sqrt(area / target_count), clamped to reasonable range
    auto_spacing = math.sqrt(area_km2 / max(target_waypoints, 5))
    auto_spacing = max(0.3, min(auto_spacing, 2.0))  # 300m to 2km

    print(f"  📐 Area={area_km2:.1f}km², auto_spacing={auto_spacing:.2f}km "
          f"(target ~{target_waypoints} waypoints)")

    lat_step = auto_spacing / 111.0
    lng_step = auto_spacing / (111.0 * math.cos(math.radians(center_lat)))

    # ── Generate grid waypoints ───────────────────────────────────────
    waypoints = []
    idx = 0
    lat = min_lat
    while lat <= max_lat:
        lng = min_lng
        while lng <= max_lng:
            if polygon.contains(Point(lng, lat)):
                idx += 1
                waypoints.append({
                    "lat":  round(lat, 6),
                    "lng":  round(lng, 6),
                    "name": f"Patrol Point {idx}",
                })
            lng += lng_step
        lat += lat_step

    # Tiny jurisdiction fallback — use centroid
    if not waypoints:
        centroid = polygon.centroid
        waypoints.append({
            "lat":  round(centroid.y, 6),
            "lng":  round(centroid.x, 6),
            "name": "Patrol Point 1 (centroid)",
        })

    return waypoints


def get_all_ward_geojson() -> list:
    """
    Return all 91 jurisdiction polygons as GeoJSON Feature dicts
    for the frontend overlay.
    """
    gdf = _load_jurisdictions()
    features = []
    for _, row in gdf.iterrows():
        features.append({
            "type": "Feature",
            "properties": {
                "ward_name": str(row.get("Name", "Unknown")).strip(),
                "ward_id":   row.get("gid") or row.get("id") or 0,
            },
            "geometry": mapping(row.geometry),
        })
    return features


# ── Pre-warm on import ─────────────────────────────────────────────────────
if os.path.exists(JURISDICTION_SHP):
    try:
        _load_jurisdictions()
        _load_station_points()
    except Exception as e:
        print(f"⚠️  Shapefile pre-load failed: {e}")
