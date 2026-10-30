import os
import requests

cities = {
    "Lahore": (31.55, 74.34),
    "Karachi": (24.86, 67.01),
    "Islamabad": (33.69, 73.05),
    "Peshawar": (34.01, 71.58),
    "Quetta": (30.18, 66.98),
}

 
def log_city(city, lat, lon):
    # 1. Get the last 5 full days (past_days=5 also returns today, so we skip today)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
        "&timezone=auto&past_days=14&forecast_days=1"
    )
    daily = requests.get(url, timeout=30).json()["daily"]

    days = []
    for i in range(5):
        days.append({
            "date": daily["time"][i],
            "max_temp": daily["temperature_2m_max"][i],
            "min_temp": daily["temperature_2m_min"][i],
            "max_wind": daily["wind_speed_10m_max"][i],
        })

    # 2. Do the maths in code, not with the AI
    avg_max = round(sum(d["max_temp"] for d in days) / 5, 1)
    avg_min = round(sum(d["min_temp"] for d in days) / 5, 1)
    peak_wind = max(d["max_wind"] for d in days)

    # 3. Ask Groq to explain the numbers
    table = "\n".join(
        f"{d['date']}: high {d['max_temp']}C, low {d['min_temp']}C, max wind {d['max_wind']} km/h"
        for d in days
    )
    prompt = (
        f"Here is the last 5 days of weather for {city}:\n{table}\n\n"
        "Write 3 short sentences for a general reader: "
        "the temperature trend, the wind pattern, and one practical tip. "
        "Use only the numbers above. Do not invent anything."
    )

    ai = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]},
        json={
            "model": "openai/gpt-oss-20b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=60,
    )
    if ai.status_code != 200:
        raise RuntimeError("Groq error: " + ai.text)
    analysis = ai.json()["choices"][0]["message"]["content"].strip()
    print(analysis)

    # 4. Send everything to your sheet script
    payload = {
        "city": city,
        "period": days[0]["date"] + " to " + days[-1]["date"],
        "avg_max": avg_max,
        "avg_min": avg_min,
        "peak_wind": peak_wind,
        "analysis": analysis,
    }
    response = requests.post(os.environ["SHEET_URL"], json=payload, timeout=30)
    print(response.status_code, response.text)

    if response.text != "OK":
        raise RuntimeError("The sheet script said: " + response.text)


failed = []
for city, (lat, lon) in cities.items():
    print(f"\n=== {city} ===")
    try:
        log_city(city, lat, lon)
    except Exception as e:
        print(f"{city} failed: {e}")
        failed.append(city)

if failed:
    raise SystemExit("Failed cities: " + ", ".join(failed))
