"""
Maintenance Team Planning — FastAPI Router
Mounted on /api/maintenance/* in main.py
Completely separate from the logistics endpoints.
"""
import io
import re
import time
from typing import List, Dict, Any, Optional

import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

from database import get_db_connection, get_warehouse_node
from geocoding import batch_geocode
from maintenance_db import (
    setup_maintenance_tables,
    insert_maintenance_tasks,
    fetch_maintenance_tasks,
    calculate_maintenance_distance_matrix,
    fetch_maintenance_route_geojson,
    fetch_maintenance_results_summary,
)
from maintenance_solver import solve_maintenance_vrp

router = APIRouter(prefix="/api/maintenance", tags=["Maintenance"])

# ──────────────────────────────────────────
# Module-level state (separate from logistics)
# ──────────────────────────────────────────

_maint_tasks_data: Optional[pd.DataFrame] = None
_maint_technicians: List[Dict[str, Any]] = []
_maint_teams: List[Dict[str, Any]] = []
_maint_office = {"latitude": 19.203000778844554, "longitude": 72.83452183056535}
_maint_return_times: Dict[int, str] = {}  # vehicle_id -> return-to-office time

# Configurable team size — default 3
MAINTENANCE_TEAM_SIZE: int = 3

TECH_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#98D8C8",
    "#F7DC6F", "#BB8FCE", "#85C1E9", "#F8B88B", "#ABEBC6"
]


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _parse_time_hhmm(val: str) -> int:
    """Parse HH:MM string to minutes from midnight."""
    m = re.match(r'(\d{1,2}):(\d{2})', str(val).strip())
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return 480  # default 08:00


def _parse_slot(slot_str: str):
    """Parse 'HH:MM to HH:MM' or 'HH:MM : HH:MM' into (start_min, end_min)."""
    s = str(slot_str).strip()
    # Try 'HH:MM to HH:MM' first
    m = re.match(r'(\d{1,2}:\d{2})\s*(?:to|-|–)\s*(\d{1,2}:\d{2})', s)
    if m:
        return _parse_time_hhmm(m.group(1)), _parse_time_hhmm(m.group(2))
    # Try 'HH:MM : HH:MM' (colon separator with spaces)
    m = re.match(r'(\d{1,2}:\d{2})\s*:\s*(\d{1,2}:\d{2})', s)
    if m:
        return _parse_time_hhmm(m.group(1)), _parse_time_hhmm(m.group(2))
    return 420, 600  # fallback


# ──────────────────────────────────────────
# Pydantic Models
# ──────────────────────────────────────────

class MaintenanceComputeRequest(BaseModel):
    team_size: int = 3
    buffer_time: int = 30
    office_lat: Optional[float] = None
    office_lon: Optional[float] = None


class MaintenanceOffice(BaseModel):
    latitude: float
    longitude: float


# ──────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────

@router.post("/upload")
async def maintenance_upload(file: UploadFile = File(...)):
    """
    Upload a 2-sheet Excel file:
      Sheet 1: Tasks — id, company name, address, MaintaiceService_time(Min), Maintaice_Time
      Sheet 2: Technicians — id, Name of person, Shift Timing
    """
    global _maint_tasks_data, _maint_technicians

    try:
        contents = await file.read()
        filename = file.filename or ""

        if not (filename.endswith('.xlsx') or filename.endswith('.xls')):
            raise HTTPException(status_code=400, detail="Please upload an .xlsx file with two sheets.")

        xls = pd.ExcelFile(io.BytesIO(contents))
        sheet_names = xls.sheet_names
        if len(sheet_names) < 2:
            raise HTTPException(status_code=400, detail="Excel must have at least 2 sheets (Tasks + Technicians).")

        # ── Sheet 1: Tasks ──
        df_tasks = pd.read_excel(xls, sheet_name=0)
        df_tasks.columns = df_tasks.columns.str.strip()

        # Normalize column names (flexible matching for varied spellings)
        col_map = {}
        for c in df_tasks.columns:
            cl = c.lower().replace(' ', '_')
            if cl == 'id':
                col_map[c] = 'id'
            elif 'company' in cl:
                col_map[c] = 'company_name'
            elif 'address' in cl:
                col_map[c] = 'address'
            elif 'service' in cl and ('time' in cl or 'min' in cl):
                col_map[c] = 'service_time'
            elif ('slot' in cl or 'avail' in cl or 'time' in cl) and 'service' not in cl:
                col_map[c] = 'slot'
        df_tasks.rename(columns=col_map, inplace=True)

        # Make sure we have the minimum columns
        if 'id' not in df_tasks.columns:
            raise HTTPException(status_code=400, detail="Sheet 1 must have an 'id' column.")
        if 'address' not in df_tasks.columns:
            raise HTTPException(status_code=400, detail="Sheet 1 must have an 'address' column.")

        # Parse slot into slot_start / slot_end
        if 'slot' in df_tasks.columns:
            parsed = df_tasks['slot'].apply(lambda x: _parse_slot(x))
            df_tasks['slot_start'] = parsed.apply(lambda x: x[0])
            df_tasks['slot_end'] = parsed.apply(lambda x: x[1])
        else:
            df_tasks['slot_start'] = 420
            df_tasks['slot_end'] = 600

        # Default service time
        if 'service_time' not in df_tasks.columns:
            df_tasks['service_time'] = 30

        # ── Geocode addresses ──
        has_coords = 'latitude' in df_tasks.columns and 'longitude' in df_tasks.columns
        if not has_coords:
            print("\n" + "=" * 60)
            print("MAINTENANCE: GEOCODING TASK ADDRESSES")
            print("=" * 60)
            addresses = df_tasks['address'].tolist()
            geocoded = await batch_geocode(addresses)
            df_tasks['latitude'] = [r.get('latitude') for r in geocoded]
            df_tasks['longitude'] = [r.get('longitude') for r in geocoded]
            failed = df_tasks[df_tasks['latitude'].isna()]
            if len(failed) > 0:
                print(f"⚠️  {len(failed)} addresses failed to geocode — dropping them.")
                df_tasks = df_tasks.dropna(subset=['latitude', 'longitude'])
            print(f"✓ Successfully geocoded {len(df_tasks)} task addresses")

        _maint_tasks_data = df_tasks

        # ── Sheet 2: Technicians ──
        df_techs = pd.read_excel(xls, sheet_name=1)
        df_techs.columns = df_techs.columns.str.strip()

        tech_col_map = {}
        for c in df_techs.columns:
            cl = c.lower()
            if cl == 'id':
                tech_col_map[c] = 'id'
            elif 'name' in cl:
                tech_col_map[c] = 'name'
            elif 'shift' in cl or 'timing' in cl:
                tech_col_map[c] = 'shift'
        df_techs.rename(columns=tech_col_map, inplace=True)

        if 'name' not in df_techs.columns:
            raise HTTPException(status_code=400, detail="Sheet 2 must have a 'Name of person' column.")

        technicians = []
        for _, row in df_techs.iterrows():
            shift_str = str(row.get('shift', '09:00 to 18:00'))
            s, e = _parse_slot(shift_str)
            technicians.append({
                "id": int(row.get('id', len(technicians) + 1)),
                "name": str(row['name']).strip(),
                "shift_start": s,
                "shift_end": e,
                "shift_label": shift_str.strip(),
            })
        _maint_technicians = technicians

        # Build response
        task_cols = ['id', 'company_name', 'address', 'service_time', 'slot']
        existing_cols = [c for c in task_cols if c in df_tasks.columns]
        task_display = df_tasks[existing_cols].fillna('').to_dict(orient='records')

        return JSONResponse(content={
            "status": "success",
            "message": f"Parsed {len(df_tasks)} tasks and {len(technicians)} technicians.",
            "tasks": task_display,
            "task_columns": existing_cols,
            "task_count": len(df_tasks),
            "technicians": technicians,
        })

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing maintenance file: {str(e)}")


@router.post("/office")
async def set_office(config: MaintenanceOffice):
    """Set the office / start-point location for maintenance routes."""
    global _maint_office
    _maint_office = {"latitude": config.latitude, "longitude": config.longitude}
    return JSONResponse(content={"status": "success", "office": _maint_office})


@router.get("/office")
async def get_office():
    return JSONResponse(content=_maint_office)


@router.post("/compute")
async def maintenance_compute(body: MaintenanceComputeRequest):
    """
    Compute optimized maintenance routes.
    Uses separate DB tables so logistics data is never touched.
    """
    global _maint_tasks_data, _maint_technicians, _maint_teams, _maint_office, MAINTENANCE_TEAM_SIZE, _maint_return_times

    if _maint_tasks_data is None or len(_maint_tasks_data) == 0:
        raise HTTPException(status_code=400, detail="No maintenance data uploaded. Upload Excel first.")
    if not _maint_technicians:
        raise HTTPException(status_code=400, detail="No technicians found in the uploaded file.")

    team_size = body.team_size or MAINTENANCE_TEAM_SIZE
    buffer_time = body.buffer_time if body.buffer_time is not None else 30
    office_lat = body.office_lat or _maint_office['latitude']
    office_lon = body.office_lon or _maint_office['longitude']

    # Persist so results / map endpoints use the correct office
    _maint_office['latitude'] = office_lat
    _maint_office['longitude'] = office_lon

    try:
        teams = []
        team_id = 1
        
        # We will just pack technicians sequentially into the requested team_size.
        # To make it slightly smarter, we could sort them by shift_start first so people on similar shifts get grouped.
        sorted_raw_techs = sorted(_maint_technicians, key=lambda x: x['shift_start'])
        
        for i in range(0, len(sorted_raw_techs), team_size):
            chunk = sorted_raw_techs[i:i+team_size]
            
            # The compound team's shift is the union of its members' shifts
            c_start = min([m['shift_start'] for m in chunk])
            c_end = max([m['shift_end'] for m in chunk])
            
            team_name = ", ".join([m['name'].split()[0] for m in chunk])
            
            # Format the compound shift label
            start_str = f"{c_start // 60:02d}:{c_start % 60:02d}"
            end_str = f"{c_end // 60:02d}:{c_end % 60:02d}"
            
            teams.append({
                "id": team_id,
                "name": team_name,
                "shift_label": f"Team {team_id} ({len(chunk)} techs) - {start_str} to {end_str}",
                "shift_start": c_start,
                "shift_end": c_end
            })
            team_id += 1
                
        _maint_teams = teams

        print("\n" + "=" * 60)
        print("MAINTENANCE: STARTING COMPUTATION")
        print("=" * 60)
        print(f"Tasks: {len(_maint_tasks_data)}, Technicians: {len(_maint_technicians)}, Teams Formed: {len(_maint_teams)}, Buffer: {buffer_time}m")

        conn = get_db_connection()

        # Step 1 — Setup maintenance tables
        print("[Step 1/4] Setting up maintenance tables...")
        setup_maintenance_tables(conn)

        # Step 2 — Insert tasks
        print("[Step 2/4] Inserting maintenance tasks...")
        t = time.perf_counter()
        insert_maintenance_tasks(conn, _maint_tasks_data)
        print(f"✓ Tasks inserted ({time.perf_counter() - t:.2f}s)")

        # Step 3 — Distance matrix
        print("[Step 3/4] Calculating distance matrix...")
        t = time.perf_counter()
        calculate_maintenance_distance_matrix(conn, office_lon, office_lat)
        print(f"✓ Distance matrix calculated ({time.perf_counter() - t:.2f}s)")

        conn.close()

        # Step 4 — Solve VRP
        print("[Step 4/4] Solving maintenance VRP...")
        result = await solve_maintenance_vrp(office_lon, office_lat, _maint_teams, len(_maint_teams), buffer_time)

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Solver failed"))

        print("\n" + "=" * 60)
        print("MAINTENANCE: COMPUTATION COMPLETED")
        print("=" * 60 + "\n")

        # Save return times for the download report
        _maint_return_times = {}
        for r in result.get('routes', []):
            _maint_return_times[r['vehicle_id']] = r.get('end_time', 'N/A')

        return JSONResponse(content={
            "status": "success",
            "message": f"Maintenance optimization completed. {result['total_technicians_used']} teams assigned {result['total_tasks_assigned']} tasks."
        })

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Maintenance computation error: {str(e)}")


@router.get("/results")
async def maintenance_results():
    """Retrieve maintenance route results."""
    global _maint_teams, _maint_office

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        team_size = len(_maint_teams) if _maint_teams else MAINTENANCE_TEAM_SIZE
        summary = fetch_maintenance_results_summary(conn, team_size)
        route_geojson = fetch_maintenance_route_geojson(conn)

        # Fetch task assignments
        cur.execute("""
            SELECT task_id, company_name, ST_X(geom) AS lon, ST_Y(geom) AS lat,
                   technician_id, service_time, arrival_time, task_status
            FROM vector.maintenance_task_node_map
            ORDER BY technician_id, arrival_time
        """)
        tasks_data = cur.fetchall()

        # Fetch unassigned
        cur.execute("""
            SELECT task_id, latitude, longitude
            FROM vector.maintenance_unassigned ORDER BY task_id
        """)
        unassigned = cur.fetchall()

        # Build per-technician routes
        vehicles = []
        parcels = []
        
        # We need the sorted teams to match IDs correctly since solve_maintenance_vrp sorts them
        sorted_teams = sorted(_maint_teams, key=lambda t: t['shift_end'] - t['shift_start'], reverse=True)
        
        for veh in summary["vehicles"]:
            vid = veh["vehicle_id"]
            v_tasks = [
                {
                    "station_id": str(t['task_id']),
                    "company_name": t['company_name'] or '',
                    "lat": float(t['lat']),
                    "lon": float(t['lon']),
                    "arrival_time": t['arrival_time'] if t['arrival_time'] else "N/A",
                    "status": t['task_status'] if t['task_status'] else "UNKNOWN",
                }
                for t in tasks_data if t['technician_id'] == vid
            ]
            if not v_tasks:
                continue

            for t in tasks_data:
                if t['technician_id'] == vid:
                    parcels.append({
                        "station_id": str(t['task_id']),
                        "lat": float(t['lat']),
                        "lon": float(t['lon']),
                        "vehicle_id": vid,
                        "color": TECH_COLORS[vid - 1] if vid <= len(TECH_COLORS) else "#888888"
                    })

            tech_name = "Technician"
            shift_start = 480
            shift_end = 1080
            if vid - 1 < len(sorted_teams):
                tech_name = sorted_teams[vid - 1]['name']
                shift_start = sorted_teams[vid - 1]['shift_start']
                shift_end = sorted_teams[vid - 1]['shift_end']

            v_geometry = [f for f in route_geojson["features"] if f["properties"]["vehicle_id"] == vid]

            vehicles.append({
                "vehicle_id": vid,
                "technician_name": f"Team {vid} ({tech_name})",
                "total_distance": float(veh["total_km"]),
                "total_tasks": int(veh["task_count"]),
                "total_service_mins": int(veh["total_service_mins"]),
                "stations": v_tasks,
                "route_geometry": v_geometry,
                "color": TECH_COLORS[vid - 1] if vid <= len(TECH_COLORS) else "#888888",
                "clock_in": f"{shift_start // 60:02d}:{shift_start % 60:02d}",
                "clock_out": f"{shift_end // 60:02d}:{shift_end % 60:02d}",
                "work_duration": shift_end - shift_start,
            })

        cur.close()
        conn.close()

        return JSONResponse(content={
            "vehicles": vehicles,
            "summary": {
                "total_distance": summary["total_distance_km"],
                "total_tasks": summary["total_tasks"],
                "total_technicians": len(vehicles),
                "office": {
                    "lat": _maint_office["latitude"],
                    "lon": _maint_office["longitude"],
                    "name": "Office"
                }
            },
            "parcels": parcels,
            "unassigned_tasks": [
                {"station_id": str(u['task_id']), "lat": float(u['latitude']), "lon": float(u['longitude'])}
                for u in unassigned
            ]
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error retrieving maintenance results: {str(e)}")


from fastapi.responses import StreamingResponse

@router.get("/download")
async def maintenance_download():
    """Download maintenance results as an Excel report."""
    global _maint_teams

    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT m.technician_id, m.task_id, m.company_name, m.service_time, m.arrival_time, m.task_status
            FROM vector.maintenance_task_node_map m
            WHERE m.technician_id IS NOT NULL
            ORDER BY m.technician_id, m.arrival_time
        """)
        tasks = cur.fetchall()
        cur.close()
        conn.close()

        if not tasks:
            raise HTTPException(status_code=400, detail="No route results found to download.")

        sorted_teams = sorted(_maint_teams, key=lambda t: t['shift_end'] - t['shift_start'], reverse=True)

        data = []
        for t in tasks:
            vid = t['technician_id']
            tech_name = "Tech " + str(vid)
            if vid - 1 < len(sorted_teams):
                tech_name = sorted_teams[vid - 1]['name']
            
            data.append({
                "Team": f"Team {vid} ({tech_name})",
                "Task ID": t['task_id'],
                "Company Name": t['company_name'],
                "Service + Buffer Time (Mins)": t['service_time'],
                "Arrival Time": t['arrival_time'],
                "Status": t['task_status'],
                "Return to Office": _maint_return_times.get(vid, 'N/A'),
            })

        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Team Assignments', index=False)
            
            # Auto-adjust columns
            worksheet = writer.sheets['Team Assignments']
            for i, col in enumerate(df.columns):
                w = max(len(col), df[col].astype(str).map(len).max() if not df.empty else 0) + 2
                worksheet.set_column(i, i, w)

        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="maintenance_team_planning.xlsx"',
            'Access-Control-Expose-Headers': 'Content-Disposition'
        }
        return StreamingResponse(
            output, 
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers=headers
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")
