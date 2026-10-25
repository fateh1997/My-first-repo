import os
import requests

cities = {
    "Lahore": (31.55, 74.34),
    "Karachi": (24.86, 67.01),
    "Islamabad": (33.69, 73.05),
    "Peshawar": (34.01, 71.58),
    "Quetta": (30.18, 66.98),
}

for city, (lat, lon) in cities.items():
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
    weather = requests.get(url, timeout=30).json()["current_weather"]
    payload = {
        "city": city,
        "temperature": weather["temperature"],
        "windspeed": weather["windspeed"],
    }

    response = requests.post(os.environ["SHEET_URL"], json=payload, timeout=30)
    print(city, response.status_code, response.text)

    if response.text != "OK":
        raise SystemExit("The sheet script said: " + response.text)
