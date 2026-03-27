# Mumbai Police Patrolling — Route Optimization System

A standalone GIS microservice that maps 91 police stations across Mumbai and generates optimized street-level patrol routes using real ward boundary polygons from official shapefile data.

---

## Features

| Feature | Description |
|---|---|
| **91 Police Stations** | Sourced from official [Mumbai Police Map](https://mumbaipolice.gov.in/Police_map) KML + Krutrim geocoding verification |
| **228 Real Ward Boundaries** | Extracted from official India Wards shapefile — proper polygon shapes, not approximations |
| **Configurable Solver Cascade** | Toggle between **Google Route Optimization**, **OR-Tools VRP**, and **OSRM** via `.env` flags |
| **Directional Route Arrows** | Patrol routes display `▶` direction markers and **START**/​**END** labels for navigation |
| **Ward Hover Highlighting** | Hover over any ward to see it highlighted in yellow with its name |
| **Street-Level Routing** | Routes follow real drivable roads — not straight lines |
| **Distance & Duration** | Each route reports total patrol distance (km) and estimated time (min) |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript, Vite, React-Leaflet |
| Backend | Python, FastAPI, Pandas, GeoPandas, Shapely |
| Solvers | OSRM (default), Google Fleet Routing, OR-Tools VRP |
| Geocoding | Krutrim / Ola Maps API (verification) |
| Map Tiles | CartoDB Light Basemap |
| Data Sources | Mumbai Police KML + India Wards Shapefile |

---

## Quick Start

### Prerequisites
- **Node.js** ≥ 18, **npm**
- **Python** ≥ 3.10 with `pip`
- Internet connection (for OSRM API and map tiles)

### 1. Backend

```bash
cd police_patrolling/backend
pip install fastapi uvicorn pandas requests geopandas shapely python-dotenv ortools
python main.py
```

API starts at **http://localhost:8001**

### 2. Frontend

```bash
cd police_patrolling/frontend
npm install
npm run dev
```

UI opens at **http://localhost:5173**

### 3. Verify/Fix Station Coordinates

```bash
cd police_patrolling/backend
python verify_stations.py
```

Uses Krutrim geocoding with strict Mumbai-bounds filtering to validate all 91 stations.

---

## Environment Configuration (`.env`)

```env
# Solver Toggle (priority: Google → VRP → OSRM → Fallback)
USE_GOOGLE_OPTIMIZATION=false
USE_VRP_SOLVER=false
USE_OSRM=true

# Google Route Optimization
GOOGLE_MAPS_API_KEY=...
GOOGLE_PROJECT_ID=...
GOOGLE_SERVICE_ACCOUNT_JSON=...

# Krutrim / Ola Maps Geocoding
KRUTRIM_API_KEY=...
KRUTRIM_PROJECT_ID=...
USE_KRUTRIM_GEOCODING=true
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check + active solver status |
| `GET` | `/api/stations` | All 91 stations with real ward assignments |
| `GET` | `/api/wards` | 228 Mumbai ward polygons as GeoJSON |
| `POST` | `/api/generate-patrol-route` | Generate optimized patrol route for a station |

### Example: Generate Patrol Route

```json
// POST /api/generate-patrol-route
{
  "station_name": "Chunabhatti PS",
  "lat": 19.056555,
  "lng": 72.872699
}

// Response
{
  "status": "success",
  "station": "Chunabhatti PS",
  "ward": "Ward No 179",
  "ward_geojson": { /* polygon */ },
  "route_geometry": [[19.056, 72.872], ...],
  "waypoint_count": 12,
  "distance_km": 8.4,
  "duration_min": 16.8,
  "solver_used": "osrm"
}
```

---

## Project Structure

```
police_patrolling/
├── backend/
│   ├── main.py                    # FastAPI server + solver cascade
│   ├── ward_processor.py          # Shapefile → ward assignment + grid waypoints
│   ├── verify_stations.py         # KML re-extraction + Krutrim verification
│   ├── generate_police_data.py    # KML→CSV extraction
│   ├── police_google_solver.py    # Google Fleet Routing adapter
│   ├── police_vrp_solver.py       # OR-Tools TSP adapter
│   ├── mumbai_police_stations.csv # 91 verified stations
│   ├── mumbai_wards.geojson       # 228 ward polygons (cached)
│   ├── ward_data/India_Wards/     # Source shapefile
│   └── .env                       # Solver + API configuration
│
├── frontend/src/
│   ├── App.tsx                    # Dashboard + ward grouping + solver badge
│   ├── PatrolMap.tsx              # Map with arrows, labels, ward overlays
│   ├── index.css                  # Premium light-mode styling
│   └── main.tsx                   # Entry point
│
├── ARCHITECTURE.md
└── README.md
```
