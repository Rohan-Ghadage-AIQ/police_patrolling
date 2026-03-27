import urllib.request
import ssl
import csv
from xml.etree import ElementTree as ET

url = "https://www.google.com/maps/d/kml?mid=1rrBfOmTh9Oe4o9qVlpyNIWfph5R82axQ&forcekml=1"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

print(f"Downloading KML from {url}...")
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, context=ctx) as response:
        kml_data = response.read()
        
    root = ET.fromstring(kml_data)
    namespace = ''
    if '}' in root.tag:
        namespace = root.tag.split('}')[0] + '}'
        
    stations = []
    
    # Detailed KML from Google MyMaps often wraps Placemarks inside a Document/Folder
    for placemark in root.iter(f'{namespace}Placemark'):
        name_node = placemark.find(f'{namespace}name')
        point_node = placemark.find(f'{namespace}Point')
        
        if name_node is not None and point_node is not None:
            name = name_node.text.strip()
            # coordinate string format typically: lng,lat,elevation
            coords_node = point_node.find(f'{namespace}coordinates')
            if coords_node is not None:
                coords_str = coords_node.text.strip()
                parts = coords_str.split(',')
                if len(parts) >= 2:
                    lng = float(parts[0])
                    lat = float(parts[1])
                    stations.append({"name": name, "lat": lat, "lng": lng})

    print(f"Extracted {len(stations)} police stations.")
    
    if stations:
        with open('mumbai_police_stations.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["name", "lat", "lng"])
            writer.writeheader()
            writer.writerows(stations)
        print("Saved to mumbai_police_stations.csv successfully.")
    else:
        print("No stations found. Structure of KML might be different.")
        
except Exception as e:
    print(f"Error fetching or parsing: {e}")
