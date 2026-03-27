import { useEffect, useMemo } from 'react';
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Polyline,
  GeoJSON,
  useMap,
} from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png';
import markerIcon from 'leaflet/dist/images/marker-icon.png';
import markerShadow from 'leaflet/dist/images/marker-shadow.png';

delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
});

const defaultIcon = new L.Icon.Default();

// Red active station icon (using SVG data URI)
const redActiveIcon = new L.Icon({
  iconUrl:
    'data:image/svg+xml;base64,' +
    btoa(`<svg xmlns="http://www.w3.org/2000/svg" width="30" height="45" viewBox="0 0 30 45">
      <path d="M15 0C6.7 0 0 6.7 0 15c0 11.3 15 30 15 30s15-18.7 15-30C30 6.7 23.3 0 15 0z" fill="#dc2626" stroke="#991b1b" stroke-width="1.5"/>
      <circle cx="15" cy="14" r="6" fill="white"/>
    </svg>`),
  iconSize: [30, 45],
  iconAnchor: [15, 45],
  popupAnchor: [0, -45],
});

// START label — positioned ABOVE the marker
const startIcon = new L.DivIcon({
  className: '',
  html: `<div class="route-label start-label">START</div>`,
  iconSize: [56, 24],
  iconAnchor: [28, 48], // pushes it above the pin
});

// END label — positioned BELOW
const endIcon = new L.DivIcon({
  className: '',
  html: `<div class="route-label end-label">END</div>`,
  iconSize: [44, 24],
  iconAnchor: [22, -4], // pushes it below
});

function createArrowIcon(bearing: number): L.DivIcon {
  return new L.DivIcon({
    className: '',
    html: `<div class="route-arrow" style="transform: rotate(${bearing}deg)">▶</div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function getBearing(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const dLng = ((lng2 - lng1) * Math.PI) / 180;
  const y = Math.sin(dLng) * Math.cos((lat2 * Math.PI) / 180);
  const x =
    Math.cos((lat1 * Math.PI) / 180) * Math.sin((lat2 * Math.PI) / 180) -
    Math.sin((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.cos(dLng);
  return (((Math.atan2(y, x) * 180) / Math.PI + 360) % 360) - 90;
}

function getArrowPositions(
  route: [number, number][],
  intervalKm: number = 0.3
): { pos: [number, number]; bearing: number }[] {
  if (route.length < 2) return [];
  const arrows: { pos: [number, number]; bearing: number }[] = [];
  let accumulated = 0;
  const intervalDeg = intervalKm / 111;
  for (let i = 1; i < route.length; i++) {
    const [lat1, lng1] = route[i - 1];
    const [lat2, lng2] = route[i];
    const segDist = Math.sqrt((lat2 - lat1) ** 2 + (lng2 - lng1) ** 2);
    accumulated += segDist;
    if (accumulated >= intervalDeg) {
      arrows.push({
        pos: [(lat1 + lat2) / 2, (lng1 + lng2) / 2],
        bearing: getBearing(lat1, lng1, lat2, lng2),
      });
      accumulated = 0;
    }
  }
  return arrows;
}

const MapUpdater = ({ center, zoom }: { center: [number, number]; zoom: number }) => {
  const map = useMap();
  useEffect(() => {
    map.flyTo(center, zoom, { duration: 1.2, easeLinearity: 0.25 });
  }, [center, zoom, map]);
  return null;
};

const wardDefaultStyle: L.PathOptions = {
  color: '#ef4444',
  weight: 1,
  fillColor: 'transparent',
  fillOpacity: 0,
  interactive: true,
};

const wardHoverStyle: L.PathOptions = {
  color: '#dc2626',
  weight: 2,
  fillColor: '#fef08a',
  fillOpacity: 0.35,
};

const activeWardStyle: L.PathOptions = {
  color: '#dc2626',
  weight: 2.5,
  fillColor: '#fee2e2',
  fillOpacity: 0.2,
};

interface Props {
  stations: any[];
  activeStation: any;
  patrolData: any;
  wardGeoJSON: any;
  onSelectStation: (station: any) => void;
}

const PatrolMap = ({ stations, activeStation, patrolData, wardGeoJSON, onSelectStation }: Props) => {
  const defaultCenter: [number, number] = [19.076, 72.8777];
  const center: [number, number] = activeStation
    ? [activeStation.lat, activeStation.lng]
    : defaultCenter;
  const zoom = patrolData ? 14 : activeStation ? 13 : 11;
  const positions: [number, number][] = patrolData?.route_geometry || [];
  const activeWardGeo = patrolData?.ward_geojson;

  const arrows = useMemo(() => getArrowPositions(positions, 0.3), [positions]);
  const routeStart = positions.length > 0 ? positions[0] : null;
  const routeEnd = positions.length > 1 ? positions[positions.length - 1] : null;

  return (
    <MapContainer
      center={defaultCenter}
      zoom={11}
      style={{ height: '100%', width: '100%' }}
      zoomControl={true}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />
      {stations.length > 0 && <MapUpdater center={center} zoom={zoom} />}

      {/* Ward boundaries */}
      {wardGeoJSON && (
        <GeoJSON
          key="all-wards"
          data={wardGeoJSON}
          style={() => wardDefaultStyle}
          onEachFeature={(feature: any, layer: any) => {
            if (feature.properties?.ward_name) {
              layer.bindTooltip(feature.properties.ward_name, {
                sticky: true,
                className: 'ward-tooltip',
                direction: 'top',
                offset: [0, -8],
              });
            }
            layer.on({
              mouseover: (e: any) => {
                e.target.setStyle(wardHoverStyle);
                e.target.bringToFront();
              },
              mouseout: (e: any) => {
                e.target.setStyle(wardDefaultStyle);
              },
            });
          }}
        />
      )}

      {/* Active ward */}
      {activeWardGeo && (
        <GeoJSON
          key={`active-ward-${activeStation?.name}-${Date.now()}`}
          data={{ type: 'Feature', geometry: activeWardGeo, properties: {} } as any}
          style={() => activeWardStyle}
        />
      )}

      {/* Route shadow */}
      {patrolData && positions.length > 0 && (
        <Polyline
          positions={positions}
          pathOptions={{ color: '#1e3a5f', weight: 7, opacity: 0.3, lineJoin: 'round', lineCap: 'round' }}
        />
      )}

      {/* Route line */}
      {patrolData && positions.length > 0 && (
        <Polyline
          positions={positions}
          pathOptions={{ color: '#2563eb', weight: 4, opacity: 0.9, lineJoin: 'round', lineCap: 'round' }}
        />
      )}

      {/* Direction arrows */}
      {patrolData &&
        arrows.map((a, i) => (
          <Marker key={`arrow-${i}`} position={a.pos} icon={createArrowIcon(a.bearing)} interactive={false} />
        ))}

      {/* START label (above the station pin) */}
      {patrolData && routeStart && (
        <Marker position={routeStart} icon={startIcon} interactive={false} />
      )}

      {/* END label (below the station pin) */}
      {patrolData && routeEnd && (
        <Marker position={routeEnd} icon={endIcon} interactive={false} />
      )}

      {/* All Police Station Markers — CLICKABLE */}
      {stations.map((s: any, idx: number) => {
        const isActive = activeStation?.name === s.name;
        return (
          <Marker
            key={idx}
            position={[s.lat, s.lng]}
            icon={isActive ? redActiveIcon : defaultIcon}
            zIndexOffset={isActive ? 1000 : 0}
            eventHandlers={{
              click: () => onSelectStation(s),
            }}
          >
            <Popup>
              <strong>{s.name}</strong>
              <br />
              <span style={{ color: '#64748b', fontSize: '12px' }}>{s.ward}</span>
            </Popup>
          </Marker>
        );
      })}
    </MapContainer>
  );
};
export default PatrolMap;
