import json              # parse/build the JSON the agent and Groq exchange
import os                # read GROQ_API_KEY / SHEET_URL from environment
import sys               # read the command-line mode (ask/dry/agent/test/...)
from time import sleep   # pause between API calls to respect rate limits

import requests           # HTTP calls to Open-Meteo, Groq, and your Sheet

CITIES = {                # city name -> (latitude, longitude)
    "Lahore": (31.55, 74.34),
    "Karachi": (24.86, 67.01),
    "Islamabad": (33.69, 73.05),
    "Peshawar": (34.01, 71.58),
    "Quetta": (30.18, 66.98),
}
# Add/remove an entry here to change which cities the job and agent cover.

MAX_STEPS = 6  # agent gets this many tool-call rounds before it's forced to stop
# Lower = stricter safety stop, may cut off a slow run too early.
# Higher = more room to think, but a stuck agent burns more API calls first.


 
def ask_groq(messages, tools=None, temperature=0.2, model="openai/gpt-oss-20b"):
    payload = {"model": model, "messages": messages, "temperature": temperature}
    if tools:                               # only attach tools when the caller passes some
        payload["tools"] = tools
        payload["tool_choice"] = "auto"     # let the model decide whether to call one
    ai = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]},
        json=payload,
        timeout=60,
    )
    if ai.status_code != 200:
        raise RuntimeError("Groq error: " + ai.text)      # fail loudly, don't guess
    return ai.json()["choices"][0]["message"]              # content + tool_calls, if any
# temperature: lower = more consistent answers, higher = more varied.
# model: swap this string to use a different Groq-hosted model.


def get_weather(city):
    lat, lon = CITIES[city]                  # look up coordinates for this city
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
        "&timezone=auto&past_days=5&forecast_days=1"   # past_days=5 = last 5 full days
    )
    daily = requests.get(url, timeout=30).json()["daily"]   # dict of parallel lists
    days = [
        {
            "date": daily["time"][i],
            "high": daily["temperature_2m_max"][i],
            "low": daily["temperature_2m_min"][i],
            "wind": daily["wind_speed_10m_max"][i],
        }
        for i in range(5)                       # 5 = how many days back; keep in sync with past_days
    ]
    return {
        "city": city,
        "period": days[0]["date"] + " to " + days[-1]["date"],
        "avg_high": round(sum(d["high"] for d in days) / 5, 1),   # 5 here must match range(5) above
        "avg_low": round(sum(d["low"] for d in days) / 5, 1),
        "peak_wind": max(d["wind"] for d in days),
        "days": days,                            # full daily breakdown, used by the agent's tool
    }


def log_city(city):
    # Same output shape as before (avg_max/avg_min/analysis), so Sheet2 and
    # the old Apps Script branch don't need to change at all.
    data = get_weather(city)
    table = "\n".join(
        f"{d['date']}: high {d['high']}C, low {d['low']}C, max wind {d['wind']} km/h"
        for d in data["days"]
    )
    prompt = (
        f"Here is the last 5 days of weather for {city}:\n{table}\n\n"
        "Write 3 short sentences for a general reader: "    # 3 = the length you're asking for
        "the temperature trend, the wind pattern, and one practical tip. "
        "Use only the numbers above. Do not invent anything."   # stops the AI making things up
    )
    msg = ask_groq([{"role": "user", "content": prompt}], temperature=0.3)   # no tools, plain Q&A
    analysis = (msg.get("content") or "").strip()
    print(analysis)
    return {
        "city": city,
        "period": data["period"],
        "avg_max": data["avg_high"],       # renamed to match the field names Sheet2 already expects
        "avg_min": data["avg_low"],
        "peak_wind": data["peak_wind"],
        "analysis": analysis,
    }

def run_job(dry=False):                   # dry=True skips posting, for safe testing
    results, failed = [], []
    for city in CITIES:                    # iterates over the dict's keys (city names)
        print(f"\n=== {city} ===")
        try:
            results.append(log_city(city))
        except Exception as e:
            print(f"{city} failed: {e}")    # one city failing doesn't stop the others
            failed.append(city)

    if dry:
        print("\nDry run. Nothing was sent to the sheet or email.")
        return                             # stop here, never reaches requests.post below

    if results:
        response = requests.post(os.environ["SHEET_URL"], json={"cities": results}, timeout=60)
        print(response.status_code, response.text)
        if response.text != "OK":            # Apps Script returns literal "OK" on success
            raise RuntimeError("The sheet script said: " + response.text)

    if failed:
        raise SystemExit("Failed cities: " + ", ".join(failed))   # non-zero exit fails the Action

    
# ---------- 5. The weather agent: the AI decides what to check and whether to alert ----------

def tool_get_weather(city):
    if city not in CITIES:
        return {"error": "city not allowed"}        # Rule 2: only listed cities/tools can run
    data = get_weather(city)
    summary = {k: v for k, v in data.items() if k != "days"}    # keep everything except the raw day list
    summary["daily_highs"] = [x["high"] for x in data["days"]]  # ...replace with just the highs
    return summary                                   # this is what the AI actually sees


def make_tool_get_history(city):
    # Binds get_history to one city per agent run, so a 5-city loop doesn't mix
    # one city's past alerts into another's (matches the &city= filter in Apps Script).
    def tool_get_history(n=5):             # n = how many past decisions (AI can choose, 1-10)
        r = requests.get(
            os.environ["SHEET_URL"],
            params={"action": "history", "n": n, "city": city},   # city is fixed here, not chosen by the AI
            timeout=30,
        )
        try:
            return r.json()
        except ValueError:                  # Apps Script returned non-JSON (likely an error page)
            return {"error": "could not read history: " + r.text[:100]}
    return tool_get_history                 # a fresh function, pre-bound to one city


AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",                      # must match the tools-dict key exactly
        "description": "Get the last 5 days of weather for a city: average high, average low, peak wind and the daily highs",
        "parameters": {"type": "object",
                        "properties": {"city": {"type": "string", "enum": list(CITIES)}},  # AI can only pick a listed city
                        "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "get_history",
        "description": "Get your own most recent decisions for this city from previous runs: time, advice, whether an alert was sent, and the alert message",
        "parameters": {"type": "object",
                        "properties": {"n": {"type": "integer", "description": "how many past runs, 1 to 10"}}}}},
]
# This list is the menu shown to the AI. Add an entry here (and to the tools
# dict passed into run_weather_agent) to give the agent a new capability.


def parse_decision(text):
    text = (text or "").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()  # strip code fences
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return None                          # not valid JSON -> caller will ask again
    ok = (isinstance(d, dict) and isinstance(d.get("advice"), str) and isinstance(d.get("reasoning"), str)
          and isinstance(d.get("send_alert"), bool) and isinstance(d.get("alert_message"), str))
    # ok = True only if all 4 required keys are present with the right types
    return d if ok else None                 # None also means "ask again"


def apply_guardrails(d):
    message = d["alert_message"].strip()[:300]         # 300 = max characters an alert can be
    send = d["send_alert"] is True and message != ""    # must be True AND have a real message
    return {"advice": d["advice"].strip(), "reasoning": d["reasoning"].strip(),
            "send_alert": send, "alert_message": message if send else ""}
# Change 300 to allow longer/shorter alert emails.
# This function is Rule 1 in code form: the AI recommends, this decides.


def run_weather_agent(city, tools, max_steps=MAX_STEPS):
    system = (                                 # instructions the AI follows for the whole run
        f"You are a weather agent for {city}. Use your tools to check the weather and "
        "your own recent decisions. "
        "Compare today with the recent days and with your past alerts. "
        "Send an alert only if something unusual or dangerous needs attention, such as extreme heat, "    # <- edit
        "strong wind or a big change, and only if the history does not show you already alerted about the same thing. "  # this block to change what counts as alert-worthy
        "Never invent numbers. If a tool returns an error, do not guess: say the data is unavailable and do not alert. "
        "When you are done, reply with JSON only, with exactly these keys: "
        "advice (one or two sentences for a general reader), "
        "reasoning (two or three sentences explaining your decision with numbers), "
        "send_alert (true or false), "
        "alert_message (a short message, or an empty string if there is no alert)."
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"Check the weather for {city} and decide what to do."}]
    used = []                                   # names of tools actually called, for the log/payload
    for step in range(1, max_steps + 1):         # hard stop at max_steps (Rule 3)
        msg = ask_groq(messages, tools=AGENT_TOOLS, temperature=0.2)
        calls = msg.get("tool_calls")
        if not calls:                            # AI didn't ask for a tool -> thinks it's done
            decision = parse_decision(msg.get("content"))
            if decision:
                return decision, used            # success: valid JSON decision
            messages.append({"role": "assistant", "content": msg.get("content") or ""})
            messages.append({"role": "user", "content": "Reply with the JSON object only, with the 4 keys."})
            continue                             # give it one more try, same step budget
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:                       # AI can ask for more than one tool at once
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            print("STEP", step, "->", name, args)    # visible trail of what the agent is doing
            fn = tools.get(name)
            if fn is None:
                result = {"error": "tool not allowed"}    # Rule 2: unlisted tool refused, not run
            else:
                try:
                    result = fn(**args)
                except TypeError:
                    result = {"error": "bad arguments"}    # AI called it with wrong/missing args
            used.append(name)
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    raise SystemExit("The agent did not finish in time")    # Rule 3: out of steps, stop the job


def run_agent_job(dry=False):
    failed = []                              # cities that errored, same pattern as run_job()
    for city in CITIES:
        print(f"\n=== {city} ===")
        try:
            tools = {"get_weather": tool_get_weather, "get_history": make_tool_get_history(city)}
            decision, used = run_weather_agent(city, tools)
            decision = apply_guardrails(decision)
            print("ADVICE:", decision["advice"])
            print("REASONING:", decision["reasoning"])
            print("ALERT:", decision["send_alert"], decision["alert_message"])

            payload = {"type": "agent", "city": city, "tools_used": ", ".join(used), **decision}
            if dry:
                print("Dry run. Nothing was sent to the sheet or email.")
                continue
            response = requests.post(os.environ["SHEET_URL"], json=payload, timeout=30)
            print(response.status_code, response.text)
            if response.text != "OK":
                raise RuntimeError("The sheet script said: " + response.text)
        except Exception as e:               # one city's timeout/error no longer kills the other 4
            print(f"{city} failed: {e}")
            failed.append(city)
        sleep(2)                              # be gentle with the free API limits across 5 cities

    if failed:
        raise SystemExit("Failed cities: " + ", ".join(failed))

def run_agent(question, city=None):
    # Not in the instructor's template — a convenience for ad hoc questions,
    # e.g. python weather_log.py ask "should I expect rain in Quetta?"
    city = city or next(iter(CITIES))          # defaults to the first city in CITIES
    tools = {"get_weather": tool_get_weather, "get_history": make_tool_get_history(city)}
    messages = [
        {"role": "system", "content": f"You are a weather agent for {city}. Use your tools to answer the user's question."},
        {"role": "user", "content": question},     # your free-text question goes straight in
    ]
    for _ in range(MAX_STEPS):                    # same step budget as the real agent
        msg = ask_groq(messages, tools=AGENT_TOOLS, temperature=0.2)
        calls = msg.get("tool_calls")
        if not calls:
            return msg.get("content") or ""         # plain text answer, no JSON required here
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            fn = tools.get(name)
            result = fn(**args) if fn else {"error": "tool not allowed"}
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    return "The agent did not finish in time."


# ---------- 6. Tests: does the agent make the right decision? ----------
TEST_CITY = next(iter(CITIES))        # picks one city to run all 5 scenarios against

SCENARIOS = [
    {"name": "Normal day",
     "weather": {"city": TEST_CITY, "avg_high": 31.0, "avg_low": 21.0, "peak_wind": 12.0,
                 "daily_highs": [30, 31, 31, 32, 31]},    # fake data fed to the AI, no real API call
     "history": [], "expect_alert": False},                # [] = no past alerts in this scenario
    {"name": "Heatwave",
     "weather": {"city": TEST_CITY, "avg_high": 43.0, "avg_low": 30.0, "peak_wind": 14.0,
                 "daily_highs": [38, 41, 43, 45, 48]},
     "history": [], "expect_alert": True},
    {"name": "Storm winds",
     "weather": {"city": TEST_CITY, "avg_high": 29.0, "avg_low": 22.0, "peak_wind": 75.0,   # 75 km/h is the trigger here
                 "daily_highs": [29, 28, 29, 30, 29]},
     "history": [], "expect_alert": True},
    {"name": "Same heatwave, already alerted",
     "weather": {"city": TEST_CITY, "avg_high": 44.0, "avg_low": 31.0, "peak_wind": 14.0,
                 "daily_highs": [44, 45, 44, 45, 46]},
     "history": [{"time": "yesterday", "city": TEST_CITY, "advice": "Extreme heat", "alert_sent": True,
                  "alert_message": "Heatwave: highs above 44 C. Stay indoors at midday."}],
     "expect_alert": False},           # tests memory: same heat, already alerted -> stay quiet
    {"name": "Weather service down",
     "weather": {"error": "service unavailable"},          # simulates the tool itself failing
     "history": [], "expect_alert": False},                 # agent must not guess when data's missing
]


def run_tests():
    passed = 0
    for s in SCENARIOS:
        tools = {"get_weather": lambda city, s=s: s["weather"],     # fake tools: ignore the AI's args,
                  "get_history": lambda n=5, s=s: s["history"]}      # always return this scenario's canned data
        decision = apply_guardrails(run_weather_agent(TEST_CITY, tools)[0])
        ok = decision["send_alert"] == s["expect_alert"]    # the actual pass/fail check
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} | {s['name']} | expected alert {s['expect_alert']}, "
              f"got {decision['send_alert']}")
        print("     ", decision["reasoning"])
        sleep(2)                        # be gentle with the free limits between scenarios
    print(f"\n{passed} of {len(SCENARIOS)} passed")




# ---------- Start here ----------
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "job"    # no argument = "job", the original behavior
    if mode == "ask":
        print(run_agent(" ".join(sys.argv[2:])))            # everything after "ask" becomes the question
    elif mode == "dry":
        run_job(dry=True)
    elif mode == "agent":
        run_agent_job()
    elif mode == "agent-dry":
        run_agent_job(dry=True)
    elif mode == "test":
        run_tests()
    else:
        run_job()