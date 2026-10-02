"""Land or Water? Ask a model about every point of a lat/lon grid, in random order, and log the answers.

    python3 land_or_water.py chat   # any OpenAI-compatible server (llama-server, vLLM...) -> answer is Land/Water (0 or 1)
    python3 land_or_water.py jev    # jev choice model (typesafe.ai) -> answer is P(land), a probability
"""
import json, math, os, random, sys, time, requests
from concurrent.futures import ThreadPoolExecutor

BACKEND = sys.argv[1] if len(sys.argv) > 1 else "chat"
CHAT_URL = os.getenv("CHAT_URL", "http://localhost:6982")
JEV_URL = "https://api.typesafe.ai/v1/systemone"
STEP = 2      # grid spacing in degrees -> 90 x 180 = 16,200 points
WORKERS = 8   # parallel requests
SEED = 0      # shuffle order
PROMPT = "Is the point at latitude {lat}, longitude {lon} on land or water?"


def ask_chat(lat, lon):
    """Generative model, fresh context per point; the grammar only allows 'Land' or 'Water'."""
    r = requests.post(CHAT_URL + "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": PROMPT.format(lat=lat, lon=lon) + " Answer with one word: Land or Water."}],
        "grammar": 'root ::= "Land" | "Water"', "max_tokens": 4, "temperature": 0,
        "cache_prompt": False, "chat_template_kwargs": {"enable_thinking": False}}).json()
    return float(r["choices"][0]["message"]["content"].strip() == "Land")


def ask_jev(lat, lon):
    """Choice model: scores both options at once; we keep P(land)."""
    r = requests.post(JEV_URL, headers={"Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"]}, json={
        "state": PROMPT.format(lat=lat, lon=lon), "model": "jev-latest",
        "questions": {"q": {"type": "choice", "instructions": "Land or water?", "criteria": {"land": None, "water": None}}}}).json()
    return r["answers"]["q"]["probabilities"]["land"]


def summarize(path):
    """Write run stats into the header line: timing, land share (area-weighted), and how unsure the model was."""
    head, *rows = open(path).read().splitlines()
    meta, pts = json.loads(head), [json.loads(r) for r in rows]
    w = [math.cos(math.radians(r["lat"])) for r in pts]
    ts = [r["t"] for r in pts if "t" in r]
    meta.update(points=len(pts), land_pct=round(100 * sum(r["p"] * x for r, x in zip(pts, w)) / sum(w), 1), earth_land_pct=29.2,
                unsure_pct=round(100 * sum(0.2 < r["p"] < 0.8 for r in pts) / len(pts), 1))  # 0 for chat (answers are 0/1)
    if ts: meta.update(started=time.strftime("%Y-%m-%d %H:%M", time.localtime(min(ts))), duration_s=round(max(ts) - min(ts)),
                       points_per_s=round(len(ts) / max(max(ts) - min(ts), 1), 1))
    open(path, "w").write("\n".join([json.dumps(meta)] + rows) + "\n")


ask = {"chat": ask_chat, "jev": ask_jev}[BACKEND]
model = "jev-latest" if BACKEND == "jev" else requests.get(CHAT_URL + "/v1/models").json()["data"][0]["id"].split("/")[-1].removesuffix(".gguf")
OUT = f"results/{model}.jsonl"
grid = [(lat + STEP / 2, lon + STEP / 2) for lat in range(-90, 90, STEP) for lon in range(-180, 180, STEP)]

os.makedirs("results", exist_ok=True)
if not os.path.exists(OUT):  # first line = run metadata, then one line per point
    open(OUT, "w").write(json.dumps({"backend": BACKEND, "model": model, "step": STEP, "total": len(grid)}) + "\n")
done = {(r["lat"], r["lon"]) for r in map(json.loads, open(OUT).readlines()[1:])}  # resume
todo = [p for p in grid if p not in done]
random.Random(SEED).shuffle(todo)


def job(p):
    try: return p, ask(*p)
    except Exception as e: print("skip", p, e); return p, None  # skipped points are retried on the next run


with open(OUT, "a") as out, ThreadPoolExecutor(WORKERS) as ex:
    for i, (p, land) in enumerate(ex.map(job, todo), 1):
        if land is not None: out.write(json.dumps({"lat": p[0], "lon": p[1], "p": round(land, 4), "t": round(time.time(), 2)}) + "\n"); out.flush()
        if i % 500 == 0: print(f"{len(done) + i}/{len(grid)}", flush=True)
summarize(OUT)
print(f"done -> {OUT}")
