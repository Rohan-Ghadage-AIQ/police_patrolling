# Architecture — Mumbai Police Patrolling System

## System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           USER (Browser)                                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  React + Vite Frontend (localhost:5173)                            │  │
│  │  ┌───────────────┐  ┌──────────────────────────────────────────┐  │  │
│  │  │   Sidebar      │  │   Leaflet Map                            │  │  │
│  │  │  Ward Groups   │  │  • Station Markers (blue pins)           │  │  │
│  │  │  Station Cards │  │  • 228 Ward Polygons (red 1px lines)     │  │  │
│  │  │  Click→Select  │  │  • Ward Hover → Yellow Highlight         │  │  │
│  │  │               │  │  • Patrol Route (blue polyline)           │  │  │
│  │  │               │  │  • Direction Arrows (▶ along route)       │  │  │
│  │  │               │  │  • START / END Labels                     │  │  │
│  │  └───────────────┘  └──────────────────────────────────────────┘  │  │
│  └────────────────────────────┬───────────────────────────────────────┘  │
│                               │ Axios HTTP                               │
└───────────────────────────────┼──────────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend (localhost:8001)                         │
│                                                                          │
│  GET /api/stations ──→ CSV + Ward Processor ──→ Stations + Ward Names   │
│  GET /api/wards    ──→ GeoJSON (228 ward polygons)                      │
│                                                                          │
│  POST /api/generate-patrol-route                                         │
│    ├─→ ward_processor.find_ward()     → Point-in-polygon ward lookup    │
│    ├─→ ward_processor.generate_waypoints() → Uniform grid inside ward   │
│    └─→ Solver Cascade:                                                   │
│         ┌─ Google Fleet Routing (if USE_GOOGLE_OPTIMIZATION=true)        │
│         ├─ OR-Tools VRP TSP    (if USE_VRP_SOLVER=true)                 │
│         ├─ OSRM Trip API       (if USE_OSRM=true) ← DEFAULT            │
│         └─ Fallback: Direct waypoint connections                        │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Data Pipeline

### Stage 1: Station Data Extraction

```
Mumbai Police Website ──→ Google My Maps KML ──→ XML Parse ──→ CSV (91 stations)
    mumbaipolice.gov.in       forcekml=1           ElementTree     mumbai_police_stations.csv
```

### Stage 2: Coordinate Verification (`verify_stations.py`)

```
KML Coordinates ──→ Krutrim Geocoding API ──→ Mumbai Bounds Filter ──→ Corrected CSV
                     api.olamaps.io/places       (18.85-19.30 lat)
                                                 (72.75-73.10 lng)
```

Only corrections where:
- Krutrim result is **within Mumbai bounds** (rejects wrong-city results)
- Offset is **> 1.5 km** from KML coordinates
- Manual user-verified overrides take priority (e.g., Chunabhatti PS)

### Stage 3: Ward Assignment (`ward_processor.py`)

```
India Wards Shapefile ──→ Filter Mumbai (228 wards) ──→ Cache as GeoJSON
    EPSG:3857 (meters)      district="Mumbai"             EPSG:4326 (lat/lng)
                                                           mumbai_wards.geojson

Station Coordinate ──→ Point-in-Polygon Test ──→ Ward Name + Polygon
    (19.056, 72.872)     Shapely .contains()       "Ward No 179"
```

### Stage 4: Patrol Waypoint Generation

```
Ward Polygon ──→ Bounding Box ──→ Uniform Grid (400m spacing) ──→ Point-in-Polygon ──→ Waypoints
                  (min/max lat/lng)   lat_step = 0.4/111          Keep only points
                                      lng_step = 0.4/(111·cos θ)  inside the polygon
```

**Why uniform grid?**
- Proportional coverage: large wards get more waypoints, small wards fewer
- No blind spots: every part of the ward is within ~200m of a waypoint
- Real roads: the solver connects waypoints via actual street paths

### Stage 5: Solver Cascade

```
                    ┌─────────────────────┐
                    │  Waypoints Generated │
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
             │ waypoints   └──────┬───────────┘  │ OSRM Trip    │  │Fallback │
             │                    │ ordered       │ Public API   │  │ Direct  │
             ▼                    ▼               │ TSP solver   │  │ Lines   │
    ┌─────────────────────────────────────────┐   └──────┬───────┘  └─────────┘
    │  OSRM Route API → Street-level geometry │          │
    │  /route/v1/driving/{ordered_coords}     │◄─────────┘
    └─────────────────────────────────────────┘
```

---

## Frontend Architecture

```
App.tsx
├── useEffect → GET /api/stations  → setStations([...])
├── useEffect → GET /api/wards     → setWardGeoJSON({...})
├── Sidebar
│   ├── Ward Groups (auto-grouped by ward_processor assignment)
│   └── Station Cards (click → setActiveStation, clear patrolData)
├── PatrolMap.tsx
│   ├── MapContainer (Leaflet + CartoDB Light tiles)
│   ├── GeoJSON (228 ward polygons — red 1px, yellow hover)
│   ├── GeoJSON (active ward — highlighted on route generation)
│   ├── Polyline Shadow (dark outline for route depth)
│   ├── Polyline (blue patrol route)
│   ├── Arrow Markers (▶ rotated by bearing, every 300m)
│   ├── START Label (green pulsing badge)
│   ├── END Label (red badge)
│   └── Station Markers (default + active with glow)
└── Control Panel (floating pill)
    ├── Station Name + Ward
    ├── "Generate Patrol Route" button → POST
    └── Status Badge (distance + solver name)
```

---

## Key Design Decisions

1. **Real ward polygons over fixed radius**: The India Wards shapefile provides accurate administrative boundaries. Patrol waypoints are generated inside the actual ward shape — larger wards get more coverage, smaller wards less.

2. **Configurable solver cascade**: Three routing engines (Google/VRP/OSRM) can be toggled via `.env` flags. This allows switching to enterprise-grade routing without code changes.

3. **Krutrim geocoding with strict filtering**: Station coordinates are verified against the Ola Maps API, but results are only applied if they fall within Mumbai's bounding box — preventing wrong-city geocoding errors.

4. **Direction arrows on routes**: Bearing-rotated `▶` markers every 300m along the polyline, plus START/END labels, make patrol routes immediately actionable for field navigation.

5. **Standalone architecture**: All solvers, data files, and APIs are self-contained — this module can be deployed independently of the parent `Gis_transportation` project.
