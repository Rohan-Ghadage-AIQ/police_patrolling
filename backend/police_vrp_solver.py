"""
Police Patrol VRP Solver — Adapted from Gis_transportation/vrp_solver.py
Uses Google OR-Tools to solve patrol TSP (single vehicle, all waypoints).
Standalone: no database, no traffic, no weather — pure routing optimization.
"""
from typing import Dict, Any, List
from ortools.constraint_solver import routing_enums_pb2, pywrapcp
import math


def _haversine_meters(lat1, lng1, lat2, lng2) -> float:
    """Calculate distance between two points in meters using Haversine formula."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def solve_patrol_vrp(
    station_lat: float,
    station_lng: float,
    waypoints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Solve patrol route using OR-Tools TSP.
    Single vehicle starts at the police station, visits all waypoints, returns.

    Returns:
        {
            "success": True/False,
            "ordered_waypoints": [...],  # waypoints in optimized visit order
            "distance_km": float,
            "duration_min": float
        }
    """
    # Build node list: [station, wp0, wp1, ..., wpN]
    all_points = [{"lat": station_lat, "lng": station_lng, "name": "Station"}] + waypoints
    n = len(all_points)

    if n <= 2:
        # Trivial: just go to the one waypoint and back
        return {
            "success": True,
            "ordered_waypoints": waypoints,
            "distance_km": sum(
                _haversine_meters(station_lat, station_lng, wp["lat"], wp["lng"])
                for wp in waypoints
            )
            * 2
            / 1000,
            "duration_min": 0,
        }

    # Build distance matrix (meters)
    dist_matrix = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                dist_matrix[i][j] = int(
                    _haversine_meters(
                        all_points[i]["lat"],
                        all_points[i]["lng"],
                        all_points[j]["lat"],
                        all_points[j]["lng"],
                    )
                )

    # OR-Tools TSP setup
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)  # 1 vehicle, depot=0
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_idx, to_idx):
        from_node = manager.IndexToNode(from_idx)
        to_node = manager.IndexToNode(to_idx)
        return dist_matrix[from_node][to_node]

    transit_cb_idx = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # Search parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = 5
    search_params.log_search = False

    solution = routing.SolveWithParameters(search_params)

    if not solution:
        return {"success": False, "error": "OR-Tools could not find a solution"}

    # Extract ordered route
    ordered = []
    total_distance = 0
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node > 0:  # Skip depot (station)
            ordered.append(waypoints[node - 1])
        next_index = solution.Value(routing.NextVar(index))
        total_distance += dist_matrix[node][manager.IndexToNode(next_index)]
        index = next_index

    # Approximate duration: patrol vehicle ~30 km/h in urban Mumbai
    distance_km = total_distance / 1000
    duration_min = (distance_km / 30) * 60  # 30 km/h average

    print(f"✅ VRP Solver: {len(ordered)} waypoints, {distance_km:.1f} km, ~{duration_min:.0f} min")

    return {
        "success": True,
        "ordered_waypoints": ordered,
        "distance_km": round(distance_km, 2),
        "duration_min": round(duration_min, 1),
    }
