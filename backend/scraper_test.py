import urllib.request
import ssl

url = "https://mumbaipolice.gov.in/Police_map"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req, context=ctx) as response:
        html = response.read().decode('utf-8')
        with open('police_map.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("Saved to police_map.html")
except Exception as e:
    print(f"Error fetching: {e}")
