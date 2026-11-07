# My First Repo

Weather logging project for Densight Labs' AI Masterclass. `weather_log.py` pulls the last 5 days of weather for a handful of Pakistani cities from Open-Meteo, asks Groq to summarize the trend, and logs the results to a Google Sheet, with an email report. It runs daily through GitHub Actions (`.github/workflows/weather.yml`).

## Setup

```
pip install -r requirements.txt
```

Set two environment variables before running:
- `SHEET_URL` — the deployed Google Apps Script web app URL
- `GROQ_API_KEY` — your Groq API key

```
python weather_log.py
```

Other modes: `dry` (job mode without posting anything), `agent` (run the agent for all 5 cities), `agent-dry` (agent mode, nothing posted), `test` (run the 5 built-in scenarios), `ask "<question>"` (ask the agent a one-off question).

## How the agent works

- **Tools.** The agent can call `get_weather` (last 5 days for a city) and `get_history` (its own recent decisions for that city, read back from the Agent tab).
- **Memory.** Each city's history is kept separate — the agent only sees its own past alerts for the city it's currently checking, so a heatwave alert for Lahore doesn't get confused with Karachi's.
- **Guardrails.** The AI only recommends; the code decides. `apply_guardrails` caps any alert message at 300 characters and blocks alerts with no message, `MAX_STEPS` stops a run that never settles on a decision, and only the two listed tools can ever be called.
- **Testing.** `python weather_log.py test` runs 5 fake-weather scenarios (normal day, heatwave, storm winds, a repeat heatwave it already alerted on, and the weather service being down) and checks the agent's alert decision against what's expected.
