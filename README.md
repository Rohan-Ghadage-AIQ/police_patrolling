# Mumbai Police Patrolling — Route Optimization System

A standalone GIS microservice that maps **91 police stations** across Mumbai and generates **optimized street-level patrol routes** within each station's official jurisdiction boundary, using authoritative shapefile data and a configurable solver cascade.

---

## Features

| Feature | Description |
|---|---|
| **91 Police Stations** | Point locations from authoritative `Point_Police_stations.shp` |
| **91 Jurisdiction Boundaries** | Official polygons from `Police_station_jurdition.shp` — one per station |
| **Territory-Enforced Routing** | Waypoints generated strictly inside each station's own jurisdiction |
| **Auto-Scaled Coverage** | Spacing auto-adjusts by area (~20 waypoints/station, feasible 30–60 min patrols) |
| **Configurable Solver Cascade** | Toggle between **Google Route Optimization**, **OR-Tools TSP**, and **OSRM** via `.env` flags |
| **Street-Level Routing** | Routes follow real drivable roads via OSRM Route API |
| **Directional Route Arrows** | `▶` direction markers every 250 m + **START** / **END** labels |
| **Live Stats Dashboard** | Distance, estimated time, average speed, solver used, waypoint count |
| **Interactive Map** | Click station pins or sidebar cards, hover jurisdictions for highlight |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript, Vite 8, React-Leaflet 5, Axios |
| Backend | Python 3.10+, FastAPI, GeoPandas, Shapely, Pydantic |
| Solvers | Google Cloud Fleet Routing (OAuth2), OR-Tools VRP, OSRM |
| Map Tiles | CartoDB Light Basemap |
| Typography | Inter (Google Fonts) |
| Data | `Point_Police_stations.shp` + `Police_station_jurdition.shp` |

---

## Quick Start

### Prerequisites

- **Node.js** ≥ 18 with **npm**
- **Python** ≥ 3.10 with **pip**
- Internet connection (for OSRM public API and map tiles)

### 1. Backend

```bash
cd backend
pip install fastapi uvicorn requests geopandas shapely python-dotenv ortools pydantic httpx google-auth
python main.py
```

API starts at **http://localhost:8001**

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI opens at **http://localhost:5173**

---

## Environment Configuration

Create `backend/.env` with the following keys:

```env
# ── Solver Toggle (priority: Google → VRP → OSRM → Fallback) ──
USE_GOOGLE_OPTIMIZATION=true
USE_VRP_SOLVER=false
USE_OSRM=false

# ── Google Route Optimization (required if USE_GOOGLE_OPTIMIZATION=true) ──
GOOGLE_MAPS_API_KEY=<your-key>
GOOGLE_PROJECT_ID=<your-project-id>
GOOGLE_SERVICE_ACCOUNT_JSON=<service-account-file.json>

# ── Krutrim / Ola Maps Geocoding (optional, not used in current module) ──
KRUTRIM_API_KEY=<your-key>
KRUTRIM_PROJECT_ID=<your-project-id>
USE_KRUTRIM_GEOCODING=true
```

> **Note:** If no solvers are enabled, the system falls back to direct waypoint-to-waypoint connections (straight lines).

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check + active solver status |
| `GET` | `/api/stations` | All 91 stations with jurisdiction assignments |
| `GET` | `/api/wards` | 91 jurisdiction polygons as GeoJSON FeatureCollection |
| `POST` | `/api/generate-patrol-route` | Generate optimized patrol route for a station |

### Example: Generate Patrol Route

```json
// POST /api/generate-patrol-route
{
  "station_name": "Chunabhatti PS",
  "lat": 19.0547,
  "lng": 72.8791
}

// Response
{
  "status": "success",
  "station": "Chunabhatti PS",
  "ward": "Chunabhatti Police Station",
  "ward_geojson": { /* jurisdiction polygon */ },
  "route_geometry": [[19.056, 72.872], ...],
  "waypoint_count": 20,
  "distance_km": 18.9,
  "duration_min": 86,
  "solver_used": "google"
}
```

---

## Project Structure

```
police_patrolling/
├── backend/
│   ├── main.py                       # FastAPI server, solver cascade, OSRM helpers
│   ├── ward_processor.py             # Shapefile loading, fuzzy jurisdiction lookup, waypoint grid
│   ├── police_google_solver.py       # Google Cloud Fleet Routing adapter (OAuth2)
│   ├── police_vrp_solver.py          # OR-Tools TSP adapter (Haversine distance matrix)
│   ├── police_route_solver.py        # (Legacy) Maintenance team planning router — not active
│   ├── generate_police_data.py       # One-off KML → CSV station extractor script
│   ├── verify_stations.py            # Geocoding verification utility
│   ├── scraper_test.py               # Scraper test utility
│   ├── ward_data/
│   │   ├── Point_Police_stations.*   # 91 station point locations (SHP + sidecar files)
│   │   └── Police_station_jurdition.*# 91 jurisdiction polygons (SHP + sidecar files)
│   ├── *.kml                         # Source KML files (Point + Jurisdiction)
│   ├── mumbai_police_stations.csv    # Generated CSV from KML extraction
│   ├── *.json                        # Google service account credentials
│   └── .env                          # Solver + API configuration
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx                   # Dashboard layout, sidebar, control bar, stats dashboard
│   │   ├── PatrolMap.tsx             # Leaflet map: markers, ward overlays, route, arrows, labels
│   │   ├── index.css                 # Full design system (Inter font, glassmorphism, animations)
│   │   ├── App.css                   # Vite boilerplate styles (unused)
│   │   └── main.tsx                  # React entry point
│   ├── index.html                    # HTML shell
│   ├── package.json                  # Dependencies (React 19, Leaflet, Axios, Vite 8)
│   ├── vite.config.ts                # Vite configuration
│   └── tsconfig*.json                # TypeScript configuration
│
├── .gitignore
├── ARCHITECTURE.md                   # Detailed system architecture document
└── README.md                         # This file
```

---

## Frontend UI Components

| Component | What it shows |
|---|---|
| **Sidebar** | Jurisdiction-grouped station list, clickable cards with coordinates |
| **Map** | 91 station pins (blue default / red active), 91 jurisdiction polygons (red borders, yellow hover) |
| **Control Bar** | Active station name + ward, "Generate Patrol Route" button |
| **Stats Dashboard** | Station, Ward, Distance (km), Est. Time, Avg Speed (km/h), Solver, Waypoints, Coordinates |
| **Route Overlay** | Blue polyline with dark shadow, bearing-rotated `▶` arrows, pulsing START + END labels |

---

## License

Internal use — Mumbai Police / AIQ project.
