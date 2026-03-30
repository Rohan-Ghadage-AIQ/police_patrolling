# Architecture — Mumbai Police Patrolling System

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           USER (Browser)                                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  React 19 + Vite 8 Frontend (localhost:5173)                       │  │
│  │  ┌───────────────┐  ┌──────────────────────────────────────────┐  │  │
│  │  │   Sidebar      │  │   Leaflet Map (React-Leaflet 5)          │  │  │
│  │  │  Ward Groups   │  │  • 91 Station Markers (blue / red active)│  │  │
│  │  │  Station Cards │  │  • 91 Jurisdiction Polygons (red 1px)    │  │  │
│  │  │  Click→Select  │  │  • Jurisdiction Hover → Yellow Highlight │  │  │
│  │  │               │  │  • Patrol Route (blue polyline + shadow)  │  │  │
│  │  │               │  │  • Direction Arrows (▶ every 250m)        │  │  │
│  │  │               │  │  • START (green pulse) / END (red) Labels │  │  │
│  │  └───────────────┘  └──────────────────────────────────────────┘  │  │
│  │                                                                    │  │
│  │  ┌─ Control Bar ──────────────────────────────────────────────┐   │  │
│  │  │  Station Name  |  Ward  |  [Generate Patrol Route]          │   │  │
│  │  └────────────────────────────────────────────────────────────┘   │  │
│  │  ┌─ Stats Dashboard ─────────────────────────────────────────┐   │  │
│  │  │  Station | Ward | Distance | Est. Time | Avg Speed |       │   │  │
│  │  │  Solver | Waypoints | Coordinates                          │   │  │
│  │  └────────────────────────────────────────────────────────────┘   │  │
│  └────────────────────────┬───────────────────────────────────────────┘  │
│                           │ Axios HTTP                                   │
└───────────────────────────┼──────────────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (localhost:8001)                         │
│                                                                          │
│  GET /api/stations ──→ Point_Police_stations.shp + name-matched ward   │
│  GET /api/wards    ──→ Police_station_jurdition.shp (91 polygons)       │
│                                                                          │
│  POST /api/generate-patrol-route                                         │
│    ├─→ ward_processor.find_jurisdiction_for_station()                   │
│    │       → Fuzzy name match → own jurisdiction polygon (enforced)     │
│    ├─→ ward_processor.generate_ward_waypoints()                         │
│    │       → Auto-scaled grid (~20 waypoints, spacing = √(area/20))     │
│    └─→ Solver Cascade:                                                   │
│         ┌─ Google Fleet Routing (if USE_GOOGLE_OPTIMIZATION=true)        │
│         ├─ OR-Tools VRP TSP    (if USE_VRP_SOLVER=true)                 │
│         ├─ OSRM Trip API       (if USE_OSRM=true)                       │
│         └─ Fallback: Direct waypoint connections                        │
│         Then: OSRM Route API → street-level geometry                    │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Data Sources

| File | Type | Records | Purpose |
|---|---|---|---|
| `ward_data/Point_Police_stations.shp` | Point (EPSG:4326) | 91 | Authoritative station locations (lat/lng) |
| `ward_data/Police_station_jurdition.shp` | Polygon (EPSG:4326) | 91 | Jurisdiction boundaries — one polygon per station |

Both shapefiles include a `Name` field. Names differ in format (e.g. `"Dindoshi PS"` vs `"Dindoshi Police Station"`) so fuzzy token-based matching is used to link them reliably.

### Supplementary Files (Backend)

| File | Purpose |
|---|---|
| `Point_Police_Station.kml` | Source KML for station points (origin data) |
| `Police_Station_Jurdition (1).kml` | Source KML for jurisdiction polygons (origin data) |
| `mumbai_police_stations.csv` | Generated CSV from KML extraction (via `generate_police_data.py`) |
| `generate_police_data.py` | One-off script to extract stations from Google MyMaps KML → CSV |
| `verify_stations.py` | Geocoding verification utility for validating station coordinates |
| `police_route_solver.py` | Legacy maintenance team planning router — **not used** by this module |

> **Legacy files in `.gitignore`:** `mumbai_wards.geojson`, `ward_data/India_Wards/` are excluded from the repository.

---

## Data Pipeline

### Stage 1: Station & Jurisdiction Loading (`ward_processor.py`)

```
Point_Police_stations.shp ──→ GeoDataFrame ──→ get_all_stations()
    EPSG:4326 (91 points)      cached in-memory   [{name, lat, lng}, ...]

Police_station_jurdition.shp ──→ GeoDataFrame ──→ get_all_ward_geojson()
    EPSG:4326 (91 polygons)      cached in-memory   [{Feature, ...}, ...]
```

Both GeoDataFrames are loaded once on import and cached at the module level (`_jurisdiction_gdf`, `_points_gdf`). If the CRS is not EPSG:4326, automatic reprojection is applied.

### Stage 2: Territory-Enforced Jurisdiction Lookup

```
Station Name + Coords
        │
        ▼
find_jurisdiction_for_station(name, lat, lng)
        │
        ├─1─ Fuzzy Name Match (token-overlap Jaccard score ≥ 0.3)
        │       normalize: lowercase, expand "PS"→"police station",
        │                  known abbrevs (BKC, JJ, VP, DB, VB, etc.)
        │       remove stop words: "police", "station", "marg", "nagar", "road"
        │       → returns OWN jurisdiction polygon (guaranteed)
        │
        ├─2─ Spatial Containment fallback (if name score < 0.3)
        │       Point-in-Polygon via Shapely .contains()
        │
        └─3─ Nearest Centroid (last resort)
                distances = all jurisdiction centroids
                → returns closest polygon
```

**Key guarantee:** A station always receives its own jurisdiction polygon — never a neighbour's territory.

### Stage 3: Patrol Waypoint Generation (Auto-Scaled)

```
Jurisdiction Polygon
        │
        ├─ Calculate area (km²) from degree² using lat-adjusted conversion
        ├─ Auto-spacing = √(area / 20), clamped to [0.3 km, 2.0 km]
        │
        ├─ Bounding box → Uniform Grid (auto-spaced, lat/lng step)
        ├─ Point-in-Polygon filter → ~20 Waypoints
        │
        └─ Tiny jurisdiction fallback: centroid as single waypoint
```

**Why auto-scaling?**
- Fixed 400 m spacing produced 100–200 waypoints for large jurisdictions → 11+ hour routes
- Auto-scaling targets ~20 waypoints → feasible 30–60 min patrol routes
- Small jurisdictions (< 3 km²) get tighter 300 m spacing for adequate coverage
- Large jurisdictions (> 20 km²) get wider spacing up to 2 km

### Stage 4: Solver Cascade

```
                    ┌─────────────────────┐
                    │  ~20 Waypoints      │
                    └─────────┬───────────┘
                              ▼
              ┌─ USE_GOOGLE_OPTIMIZATION=true? ─┐
              │ YES                         NO  │
              ▼                                 ▼
    ┌─────────────────┐              ┌─ USE_VRP_SOLVER=true? ─┐
    │ Google Fleet    │              │ YES                 NO  │
    │ Routing API     │              ▼                         ▼
    │ (OAuth2 + SA)   │    ┌──────────────────┐    ┌─ USE_OSRM=true? ─┐
    └────────┬────────┘    │ OR-Tools TSP     │    │ YES          NO  │
             │             │ Haversine matrix │    ▼                   ▼
             │ ordered     │ 5s time limit    │  ┌──────────────┐  ┌─────────┐
             │ waypoints   │ GLS metaheuristic│  │ OSRM Trip    │  │Fallback │
             │             └──────┬───────────┘  │ Public API   │  │ Direct  │
             │                    │ ordered       │ TSP solver   │  │ Lines   │
             ▼                    ▼               │ roundtrip    │  └─────────┘
    ┌─────────────────────────────────────────┐   └──────┬───────┘
    │  OSRM Route API → Street-level geometry │          │
    │  /route/v1/driving/{ordered_coords}     │◄─────────┘
    │  ?geometries=geojson&overview=full       │
    └─────────────────────────────────────────┘
```

**Solver details:**

| Solver | Method | Notes |
|---|---|---|
| **Google Fleet Routing** | Cloud API (OAuth2 service account) | Single vehicle, 1 min stops, `costPerKilometer=1.0` |
| **OR-Tools VRP** | TSP with Haversine distance matrix | `PATH_CHEAPEST_ARC` + `GUIDED_LOCAL_SEARCH`, 5 s limit |
| **OSRM Trip** | Public API at `router.project-osrm.org` | `roundtrip=true`, `source=first` |
| **Fallback** | Direct waypoint connections | Straight lines, no road snapping |

After any solver orders the waypoints, OSRM Route API provides actual street-level geometry (GeoJSON coordinates → `[[lat, lng], ...]`).

---

## Frontend Architecture

### Component Tree

```
App.tsx
├── useEffect → GET /api/stations  → setStations([...])
├── useEffect → GET /api/wards     → setWardGeoJSON({...})
├── Sidebar
│   ├── Sidebar Header ("Mumbai Police Patrolling" + subtitle)
│   ├── Jurisdiction Groups (auto-grouped by ward_processor assignment)
│   └── Station Cards (click → setActiveStation, clear patrolData)
├── PatrolMap.tsx
│   ├── MapContainer (Leaflet + CartoDB Light tiles)
│   ├── MapUpdater (flyTo animation on station/route change)
│   ├── GeoJSON — All Wards (91 jurisdiction polygons, red 1px, hover → yellow)
│   ├── GeoJSON — Active Ward (highlighted on route generation)
│   ├── Polyline Shadow (dark #1e3a5f outline, weight 7, opacity 0.3)
│   ├── Polyline Route (blue #2563eb, weight 4, opacity 0.9)
│   ├── Arrow Markers (▶ rotated by bearing, every 250m, 100m min segment filter)
│   ├── START Label (green #16a34a pulsing badge, positioned above pin)
│   ├── END Label (red #dc2626 badge, positioned below pin)
│   └── Station Markers (default blue Leaflet icon + active red SVG icon)
├── Control Bar (floating pill, bottom-center)
│   ├── Active Station Name + Ward Name
│   └── "Generate Patrol Route" button (disabled after route generation)
└── Stats Dashboard (bottom bar, visible after route generation)
    ├── Station Name
    ├── Ward Name
    ├── Distance (km)
    ├── Estimated Time (min or h:m format)
    ├── Average Speed (km/h) — computed as distance / time
    ├── Solver Used (badge: GOOGLE / VRP / OSRM / FALLBACK)
    ├── Waypoint Count
    └── Coordinates (lat, lng of active station)
```

### Zoom Behaviour

| State | Zoom Level |
|---|---|
| No station selected | 11 (city overview) |
| Station selected | 13 (neighbourhood) |
| Route generated | 14 (street level) |

Transitions use `flyTo()` with 1.2 s duration and `easeLinearity: 0.25`.

### Styling

The UI uses a custom design system defined in `index.css`:
- **Font**: Inter (Google Fonts) with `-apple-system` fallback
- **Glassmorphism**: `backdrop-filter: blur(24px)` on sidebar and control panels
- **Animations**: `slideUp` (0.4 s) for panels, `pulse-start` (2 s) for START label, `spin` (0.8 s) for loading spinner
- **Ward hover**: Red border + yellow `#fef08a` fill at 35% opacity
- **Active station card**: Blue gradient (`#eff6ff` → `#dbeafe`) with blue `#2563eb` border

---

## Key Design Decisions

1. **Authoritative shapefiles over derived data**: `Police_station_jurdition.shp` and `Point_Police_stations.shp` replace the old approach of filtering an India-wide shapefile by a Mumbai bounding box. Each polygon is the exact assigned territory of its station.

2. **Territory enforcement via name-based jurisdiction lookup**: `find_jurisdiction_for_station()` uses normalised token-overlap matching (Jaccard ≥ 0.3 with stop-word removal) so each station always resolves to its own polygon — never a neighbour's — even when the names differ between the two shapefiles (e.g. `"Varsova PS"` → `"Versova Police Station"`).

3. **Auto-scaled waypoint spacing**: `spacing = √(area / 20)` clamped to [0.3 km, 2.0 km] ensures every jurisdiction gets ~20 waypoints regardless of size. This produces feasible 30–60 minute patrol routes instead of the 11+ hour routes that fixed 400 m spacing created for large jurisdictions.

4. **Configurable solver cascade**: Three routing engines (Google/VRP/OSRM) can be toggled via `.env` flags with automatic fallback, allowing enterprise-grade routing without code changes.

5. **Real road geometry**: Solvers order the waypoints, then OSRM Route API (`/route/v1/driving/`) provides actual street-level geometry with `geometries=geojson&overview=full`. Routes follow drivable roads, not straight lines.

6. **Direction arrows with anti-glitch filtering**: Bearing-rotated `▶` markers every 250 m along the polyline, with a 100 m minimum segment filter (`minSegDeg = 0.0009`) to prevent bidirectional arrow glitching on micro-segments. START (green pulsing) + END (red) labels mark route terminals.

7. **Two-layer route rendering**: A dark shadow polyline (`#1e3a5f`, weight 7, opacity 0.3) renders beneath the blue route polyline (`#2563eb`, weight 4) for depth and readability.

8. **Standalone architecture**: All solvers, data files, and APIs are self-contained — this module can be deployed independently with zero external database dependencies. All data is loaded from shapefiles and cached in-memory.

9. **Computed patrol stats**: The frontend computes average speed (`distance / time`) and formats duration intelligently (minutes for < 60 min, hours + minutes otherwise). If the solver does not return duration, it estimates using 25 km/h average patrol speed.

---

## Backend File Inventory

| File | Status | Purpose |
|---|---|---|
| `main.py` | **Active** | FastAPI app, CORS, endpoints, solver cascade, OSRM helpers |
| `ward_processor.py` | **Active** | Shapefile I/O, fuzzy matching, waypoint generation |
| `police_google_solver.py` | **Active** | Google Fleet Routing with OAuth2 service account auth |
| `police_vrp_solver.py` | **Active** | OR-Tools TSP with Haversine distance matrix |
| `police_route_solver.py` | **Legacy** | Maintenance team planning router (from another project, not imported) |
| `generate_police_data.py` | **Utility** | One-off KML → CSV extractor for station data |
| `verify_stations.py` | **Utility** | Geocoding verification for station coordinates |
| `scraper_test.py` | **Utility** | Scraper test script |
