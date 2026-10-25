import os
import requests

cities = {
    "Lahore": (31.55, 74.34),
    "Karachi": (24.86, 67.01),
    "Islamabad": (33.69, 73.05),
    "Peshawar": (34.01, 71.58),
    "Quetta": (30.18, 66.98),
}


def get_weather(lat, lon, attempts=3):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
    for attempt in range(1, attempts + 1):
        try:
            return requests.get(url, timeout=20).json()["current_weather"]
        except requests.exceptions.RequestException as e:
            print(f"  attempt {attempt}/{attempts} failed: {e}")
    return None


failed = []
for city, (lat, lon) in cities.items():
    weather = get_weather(lat, lon)
    if weather is None:
        failed.append(city)
        continue

    payload = {
        "city": city,
        "temperature": weather["temperature"],
        "windspeed": weather["windspeed"],
    }

    response = requests.post(os.environ["SHEET_URL"], json=payload, timeout=30)
    print(city, response.status_code, response.text)

    if response.text != "OK":
        raise SystemExit("The sheet script said: " + response.text)

if failed:
    raise SystemExit("Could not fetch weather for: " + ", ".join(failed))
