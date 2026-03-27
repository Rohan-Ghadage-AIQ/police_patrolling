import { useState, useEffect } from 'react';
import axios from 'axios';
import PatrolMap from './PatrolMap';
import './index.css';

interface Station {
  name: string;
  lat: number;
  lng: number;
  ward?: string;
}

interface PatrolData {
  station: string;
  ward: string;
  ward_geojson?: any;
  route_geometry?: [number, number][];
  waypoint_count?: number;
  distance_km?: number;
  duration_min?: number;
  solver_used?: string;
  note?: string;
}

const AVG_PATROL_SPEED_KMH = 25; // avg patrol vehicle speed in urban Mumbai

const App = () => {
  const [stations, setStations] = useState<Station[]>([]);
  const [activeStation, setActiveStation] = useState<Station | null>(null);
  const [patrolData, setPatrolData] = useState<PatrolData | null>(null);
  const [wardGeoJSON, setWardGeoJSON] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    axios
      .get('http://localhost:8001/api/stations')
      .then((res) => {
        if (res.data.status === 'success') {
          setStations(res.data.data);
          if (res.data.data.length > 0) setActiveStation(res.data.data[0]);
        }
      })
      .catch((err) => console.error('Failed to load stations', err));

    axios
      .get('http://localhost:8001/api/wards')
      .then((res) => {
        if (res.data.status === 'success') setWardGeoJSON(res.data);
      })
      .catch((err) => console.error('Failed to load wards', err));
  }, []);

  const handleSelectStation = (station: Station) => {
    setActiveStation(station);
    setPatrolData(null);
  };

  const generatePatrol = async () => {
    if (!activeStation) return;
    setLoading(true);
    try {
      const res = await axios.post('http://localhost:8001/api/generate-patrol-route', {
        station_name: activeStation.name,
        lat: activeStation.lat,
        lng: activeStation.lng,
      });
      if (res.data.status === 'success') setPatrolData(res.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const wards = Array.from(new Set(stations.map((s) => s.ward || 'General')));

  // Compute patrol stats
  const distKm = patrolData?.distance_km || 0;
  const durationMin = patrolData?.duration_min
    ? patrolData.duration_min
    : distKm > 0
      ? (distKm / AVG_PATROL_SPEED_KMH) * 60
      : 0;
  const avgSpeed = durationMin > 0 ? (distKm / (durationMin / 60)) : 0;

  return (
    <div className="dashboard">
      <div className="sidebar">
        <div className="sidebar-header">
          <h1>Mumbai Police Patrolling</h1>
          <p>Territorial Street-Level Routing</p>
        </div>
        <div className="station-list">
          {wards.map((wardName, wIdx) => (
            <div key={wIdx} className="ward-group">
              <div className="ward-header">{wardName}</div>
              {stations
                .filter((s) => (s.ward || 'General') === wardName)
                .map((s, i) => (
                  <div
                    key={i}
                    className={`station-card ${activeStation?.name === s.name ? 'active' : ''}`}
                    onClick={() => handleSelectStation(s)}
                  >
                    <h3>{s.name}</h3>
                    <p>
                      {s.lat.toFixed(4)}, {s.lng.toFixed(4)}
                    </p>
                  </div>
                ))}
            </div>
          ))}
        </div>
      </div>

      <div className="map-container">
        <PatrolMap
          stations={stations}
          activeStation={activeStation}
          patrolData={patrolData}
          wardGeoJSON={wardGeoJSON}
          onSelectStation={handleSelectStation}
        />

        {/* Top control bar */}
        {activeStation && (
          <div className="control-panel">
            <div>
              <div style={{ fontWeight: 700, color: '#0f172a', fontSize: '15px' }}>
                {activeStation.name}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {activeStation.ward || 'General'}
              </div>
            </div>

            <button
              className="btn-primary"
              onClick={generatePatrol}
              disabled={loading || patrolData !== null}
            >
              {loading ? (
                <span className="loading-spinner"></span>
              ) : patrolData ? (
                'Route Active'
              ) : (
                'Generate Patrol Route'
              )}
            </button>
          </div>
        )}

        {/* Bottom stats dashboard */}
        {patrolData && (
          <div className="stats-dashboard">
            <div className="stat-item">
              <div className="stat-label">Station</div>
              <div className="stat-value">{patrolData.station}</div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Ward</div>
              <div className="stat-value">{patrolData.ward}</div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Distance</div>
              <div className="stat-value highlight">
                {distKm > 0 ? `${distKm.toFixed(1)} km` : '—'}
              </div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Est. Time</div>
              <div className="stat-value highlight">
                {durationMin > 0
                  ? durationMin >= 60
                    ? `${Math.floor(durationMin / 60)}h ${Math.round(durationMin % 60)}m`
                    : `${Math.round(durationMin)} min`
                  : '—'}
              </div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Avg Speed</div>
              <div className="stat-value">{avgSpeed > 0 ? `${avgSpeed.toFixed(0)} km/h` : '—'}</div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Solver</div>
              <div className="stat-value solver-badge">
                {patrolData.solver_used?.toUpperCase() || 'N/A'}
              </div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Waypoints</div>
              <div className="stat-value">{patrolData.waypoint_count || '—'}</div>
            </div>
            <div className="stat-divider" />
            <div className="stat-item">
              <div className="stat-label">Address</div>
              <div className="stat-value address">
                {activeStation?.lat.toFixed(4)}, {activeStation?.lng.toFixed(4)}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
export default App;
