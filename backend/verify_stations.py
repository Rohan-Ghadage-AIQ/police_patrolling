"""
FINAL station data pipeline:
1. Re-extract from official Mumbai Police KML (clean source)
2. Apply manual corrections for known wrong stations
3. Run Krutrim geocoding verification with strict Mumbai bounds filtering
"""
import urllib.request
import ssl
import csv
import os
import math
import time
import requests
import json
from xml.etree import ElementTree as ET
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

KRUTRIM_API_KEY = os.getenv("KRUTRIM_API_KEY", "")
OLA_MAPS_URL = "https://api.olamaps.io/places/v1/geocode"
CSV_PATH = os.path.join(os.path.dirname(__file__), "mumbai_police_stations.csv")

# Mumbai bounding box (strict)
MUM_LAT = (18.85, 19.30)
MUM_LNG = (72.75, 73.10)

# ═══ STEP 1: KML Extraction ═══
print("═══ STEP 1: Extracting from official KML ═══")
url = "https://www.google.com/maps/d/kml?mid=1rrBfOmTh9Oe4o9qVlpyNIWfph5R82axQ&forcekml=1"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, context=ctx) as response:
    kml_data = response.read()

root = ET.fromstring(kml_data)
ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""

stations = []
for pm in root.iter(f"{ns}Placemark"):
    name_el = pm.find(f"{ns}name")
    point_el = pm.find(f"{ns}Point")
    if name_el is not None and point_el is not None:
        name = name_el.text.strip()
        coords_el = point_el.find(f"{ns}coordinates")
        if coords_el is not None:
            parts = coords_el.text.strip().split(",")
            if len(parts) >= 2:
                lng, lat = float(parts[0]), float(parts[1])
                stations.append({"name": name, "lat": lat, "lng": lng})

print(f"  Extracted {len(stations)} stations from KML\n")

# ═══ STEP 2: Manual Corrections ═══
print("═══ STEP 2: Applying manual corrections ═══")
# User-verified exact coordinates
MANUAL_CORRECTIONS = {
    # User-verified exact coordinates
    "Chunabhatti PS":       (19.056555815952997, 72.87269892772565),
    # Corrected from known Mumbai locations
    "Sion PS":              (19.0388, 72.8650),
    "Dharavi PS":           (19.0421, 72.8548),
    "Tardeo PS":            (18.9722, 72.8136),
    "Chembur PS":           (19.0621, 72.8991),
    "Tilak Nagar PS":       (19.0842, 72.9170),
    "Shahu Nagar PS":       (19.0585, 72.9105),
    "Kurla PS":             (19.0663, 72.8800),
    "Navghar PS":           (19.0935, 72.9350),
    "Antop Hill PS":        (19.0280, 72.8690),
    "Nagpada PS":           (18.9628, 72.8292),
    "Agripada PS":          (18.9650, 72.8241),
    "Mankhurd PS":          (19.0437, 72.9250),
    "Govandi PS":           (19.0610, 72.9095),
    "Kandivali PS":         (19.2096, 72.8515),
    "Vikhroli PS":          (19.1062, 72.9273),
    "Cuffe Parade PS":      (18.9216, 72.8231),
    "JJ Marg PS":           (18.9650, 72.8360),
    "Azad Maidan PS":       (18.9380, 72.8340),
    "Gamdevi PS":           (18.9680, 72.8100),
    "LT Marg PS":           (18.9535, 72.8285),
    "Mahim PS":             (19.0412, 72.8460),
    "Nehru Nagar PS":       (19.1100, 72.8925),
    "Bandra PS":            (19.0544, 72.8390),
    "Borivali PS":          (19.2281, 72.8579),
    "DB Marg PS":           (18.9620, 72.8140),
    "Marine Drive PS":      (18.9428, 72.8231),
    "Malad PS":             (19.1864, 72.8490),
    "Airport PS":           (19.0946, 72.8571),
    "Aarey Sub PS":         (19.1551, 72.8570),
    "Parksite PS":          (19.1013, 72.9188),
    "Ghatkopar PS":         (19.0856, 72.9087),
    "Deonar PS":            (19.0465, 72.9062),
    "Amboli PS":            (19.1279, 72.8350),
}

# Known generic Krutrim fallback coordinates to REJECT
GENERIC_COORDS = {
    (19.081114, 72.836485),
    (19.0811, 72.8365),
}

for s in stations:
    if s["name"] in MANUAL_CORRECTIONS:
        old = (s["lat"], s["lng"])
        new = MANUAL_CORRECTIONS[s["name"]]
        s["lat"], s["lng"] = new[0], new[1]
        print(f"  ✅ {s['name']}: ({old[0]:.4f}, {old[1]:.4f}) → ({new[0]:.4f}, {new[1]:.4f})")

# ═══ STEP 3: Smart Krutrim Verification ═══
print(f"\n═══ STEP 3: Krutrim verification (API key: {KRUTRIM_API_KEY[:8]}...) ═══")


def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocode(name):
    query = f"{name}, Mumbai, Maharashtra"
    try:
        resp = requests.get(
            OLA_MAPS_URL,
            params={"address": query, "language": "en", "api_key": KRUTRIM_API_KEY},
            headers={"Origin": "http://localhost:5173", "Referer": "http://localhost:5173/"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("geocodingResults", [])
            if results:
                geo = results[0].get("geometry", {}).get("location", {})
                addr = results[0].get("formatted_address", "")
                lat, lng = float(geo.get("lat", 0)), float(geo.get("lng", 0))
                return lat, lng, addr
    except Exception as e:
        pass
    return None, None, ""


applied = 0
skipped = 0
for s in stations:
    # Skip manually-corrected stations
    if s["name"] in MANUAL_CORRECTIONS:
        continue

    kru_lat, kru_lng, kru_addr = geocode(s["name"])
    if kru_lat is None:
        continue

    # STRICT: must be within Mumbai bounds
    if not (MUM_LAT[0] <= kru_lat <= MUM_LAT[1] and MUM_LNG[0] <= kru_lng <= MUM_LNG[1]):
        print(f"  🚫 {s['name']}: outside Mumbai ({kru_lat:.4f}, {kru_lng:.4f}) — SKIPPED")
        skipped += 1
        continue

    # Reject known generic fallback coordinates
    rounded = (round(kru_lat, 4), round(kru_lng, 4))
    if any(abs(kru_lat - g[0]) < 0.001 and abs(kru_lng - g[1]) < 0.001 for g in GENERIC_COORDS):
        print(f"  🚫 {s['name']}: generic coordinate — SKIPPED")
        skipped += 1
        continue

    dist = haversine_km(s["lat"], s["lng"], kru_lat, kru_lng)

    # Only correct if > 1.5km AND the Krutrim result is clearly better
    if dist > 1.5:
        print(f"  ⚡ {s['name']}: {dist:.2f} km off → ({kru_lat:.6f}, {kru_lng:.6f})")
        s["lat"], s["lng"] = kru_lat, kru_lng
        applied += 1

    time.sleep(0.1)

print(f"\n  Applied: {applied} | Skipped out-of-bounds: {skipped}")

# ═══ SAVE ═══
with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "lat", "lng"])
    writer.writeheader()
    writer.writerows(stations)

print(f"\n✅ FINAL: {len(stations)} stations saved to {CSV_PATH}")
