import glob, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request
import anthropic, bcrypt, stripe, yaml
from dotenv import load_dotenv
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from itsdangerous import BadSignature, URLSafeSerializer

load_dotenv()
SECRET = os.environ.get("SECRET_KEY", "dev-change-me")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
DB = os.environ.get("DB_PATH", "app.db")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
FREE_AI, FREE_SAVES = 5, 2
THEMES = ["classic", "sb2nov", "moderncv", "engineeringresumes", "engineeringclassic"]
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
signer = URLSafeSerializer(SECRET)
app = FastAPI()


MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def norm_date(v, allow_present=False):
    """Turn what people actually type into RenderCV's YYYY / YYYY-MM / YYYY-MM-DD."""
    s = str(v or "").strip()
    if not s:
        return ""
    low = s.lower().strip(" .")
    if allow_present and low in ("present", "current", "now", "ongoing", "today"):
        return "present"
    m = re.fullmatch(r"(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        out = y
        if mo:
            out += "-%02d" % max(1, min(12, int(mo)))
        if d:
            out += "-%02d" % max(1, min(28, int(d)))
        return out
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s*'?(\d{2,4})", s)
    if m and m.group(1)[:3].lower() in MONTHS:
        y = int(m.group(2))
        y = y + 2000 if y < 100 else y
        return "%d-%02d" % (y, MONTHS[m.group(1)[:3].lower()])
    m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", s)
    if m:
        return "%s-%02d" % (m.group(2), max(1, min(12, int(m.group(1)))))
    return s


def norm_url(v):
    s = str(v or "").strip()
    if not s:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s) and not s.lower().startswith("mailto:"):
        s = "https://" + s.lstrip("/")
    return s


def clean_err(text):
    """Strip RenderCV's box drawing and ANSI so the reader sees the sentence."""
    t = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    t = re.sub(r"[│╭╮╰╯─┬┴┼├┤┌┐└┘]", " ", t)
    lines = [" ".join(l.split()) for l in t.splitlines()]
    lines = [l for l in lines if l and set(l) != {"|"}]
    return " ".join(lines)[:600]


def q(sql, a=(), one=False):
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        cur = c.execute(sql, a)
        c.commit()
        if sql.lstrip().lower().startswith(("insert", "update", "delete")):
            return cur.lastrowid
        return cur.fetchone() if one else cur.fetchall()
    finally:
        c.close()


q("""create table if not exists users(id integer primary key, email text unique, pw blob,
     plan text default 'free', ai_used integer default 0, customer text)""")
q("""create table if not exists resumes(id integer primary key, user_id integer, title text,
     template text, data text, created text default current_timestamp)""")


def user(req: Request):
    try:
        uid = signer.loads(req.cookies.get("s", ""))
    except BadSignature:
        raise HTTPException(401, "Log in to continue")
    u = q("select * from users where id=?", (uid,), one=True)
    if not u:
        raise HTTPException(401, "Log in to continue")
    return u


def pub(u):
    left = None if u["plan"] == "pro" else max(0, FREE_AI - u["ai_used"])
    return {"email": u["email"], "plan": u["plan"], "ai_left": left,
            "ai_enabled": ai_backend() is not None}


def session(res, uid):
    res.set_cookie("s", signer.dumps(uid), httponly=True, samesite="lax",
                   secure=APP_URL.startswith("https"), max_age=2592000)
    return pub(q("select * from users where id=?", (uid,), one=True))


@app.post("/api/signup")
def signup(b: dict, res: Response):
    email, pw = b.get("email", "").strip().lower(), b.get("password", "")
    if "@" not in email or len(pw) < 8:
        raise HTTPException(400, "Enter a valid email and a password of 8+ characters")
    try:
        uid = q("insert into users(email,pw) values(?,?)", (email, bcrypt.hashpw(pw.encode(), bcrypt.gensalt())))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "That email is already registered. Log in instead")
    return session(res, uid)


@app.post("/api/login")
def login(b: dict, res: Response):
    u = q("select * from users where email=?", (b.get("email", "").strip().lower(),), one=True)
    if not u or not bcrypt.checkpw(b.get("password", "").encode(), u["pw"]):
        raise HTTPException(401, "Wrong email or password")
    return session(res, u["id"])


@app.post("/api/logout")
def logout(res: Response):
    res.delete_cookie("s")
    return {"ok": True}


@app.get("/api/me")
def me(u=Depends(user)):
    return pub(u)


# ---------- saved versions ----------
@app.get("/api/resumes")
def list_resumes(u=Depends(user)):
    rows = q("select id,title,template,created from resumes where user_id=? order by id desc", (u["id"],))
    return [dict(r) for r in rows]


@app.get("/api/resumes/{rid}")
def get_resume(rid: int, u=Depends(user)):
    r = q("select * from resumes where id=? and user_id=?", (rid, u["id"]), one=True)
    if not r:
        raise HTTPException(404, "Not found")
    return {"id": rid, "title": r["title"], "template": r["template"], "data": json.loads(r["data"])}


@app.post("/api/resumes")
def save_resume(b: dict, u=Depends(user)):
    vals = (b.get("title") or "Untitled", b.get("template", "classic"), json.dumps(b.get("data", {})))
    if b.get("id"):
        q("update resumes set title=?,template=?,data=? where id=? and user_id=?", vals + (b["id"], u["id"]))
        return {"id": b["id"]}
    n = q("select count(*) c from resumes where user_id=?", (u["id"],), one=True)["c"]
    if u["plan"] != "pro" and n >= FREE_SAVES:
        raise HTTPException(402, f"Free plan keeps {FREE_SAVES} versions. Delete one or upgrade to Pro")
    return {"id": q("insert into resumes(title,template,data,user_id) values(?,?,?,?)", vals + (u["id"],))}


@app.delete("/api/resumes/{rid}")
def del_resume(rid: int, u=Depends(user)):
    q("delete from resumes where id=? and user_id=?", (rid, u["id"]))
    return {"ok": True}


# ---------- RenderCV bridge ----------
def to_yaml(d, template):
    cv = {"name": d.get("name") or "Your Name"}
    for k in ("location", "email", "phone", "website"):
        if d.get(k):
            cv[k] = norm_url(d[k]) if k == "website" else d[k].strip()
    sec = {}
    if d.get("summary"):
        sec["Summary"] = [d["summary"].strip()]
    exp = []
    for e in d.get("experience", []):
        if not e.get("company"):
            continue
        x = {"company": e["company"], "position": e.get("position", "")}
        if e.get("location"):
            x["location"] = e["location"]
        st, en = norm_date(e.get("start")), norm_date(e.get("end"), allow_present=True)
        if st:
            x["start_date"] = st
        if en:
            x["end_date"] = en
        if not st and not en and e.get("date"):
            x["date"] = str(e["date"]).strip()
        hl = [l.strip(" -•\t") for l in e.get("bullets", "").splitlines() if l.strip()]
        if hl:
            x["highlights"] = hl
        exp.append(x)
    edu = []
    for e in d.get("education", []):
        if not e.get("institution"):
            continue
        x = {"institution": e["institution"]}
        for k, v in (("degree", "degree"), ("area", "area")):
            if e.get(v):
                x[k] = e[v]
        st, en = norm_date(e.get("start")), norm_date(e.get("end"), allow_present=True)
        if st:
            x["start_date"] = st
        if en:
            x["end_date"] = en
        if not (st or en) and e.get("date"):
            x["date"] = e["date"]
        edu.append(x)
    sk = [{"label": s["label"], "details": s.get("details", "")} for s in d.get("skills", []) if s.get("label")]
    for name, val in (("Experience", exp), ("Education", edu), ("Skills", sk)):
        if val:
            sec[name] = val
    cv["sections"] = sec
    theme = template if template in THEMES else "classic"
    design = {"theme": theme, "page": {"show_top_note": False},
              "header": {"connections": {"phone_number_format": "international"}}}
    return yaml.safe_dump({"cv": cv, "design": design}, sort_keys=False, allow_unicode=True)


def render_pdf(d, template):
    with tempfile.TemporaryDirectory() as t:
        with open(f"{t}/resume.yaml", "w", encoding="utf-8") as f:
            f.write(to_yaml(d, template))
        exe = shutil.which("rendercv")
        cmd = [exe] if exe else [sys.executable, "-m", "rendercv"]
        p = subprocess.run(cmd + ["render", "resume.yaml"], cwd=t, capture_output=True, text=True, timeout=120)
        pdfs = glob.glob(f"{t}/**/*.pdf", recursive=True)
        if p.returncode or not pdfs:
            raise HTTPException(422, clean_err((p.stdout or "") + (p.stderr or "")) or "PDF render failed")
        with open(pdfs[0], "rb") as f:
            return f.read()


@app.post("/api/render")
def render(b: dict, u=Depends(user)):
    return Response(render_pdf(b.get("data", {}), b.get("template", "classic")), media_type="application/pdf")


# ---------- AI ----------
RULES = ("You are an expert resume writer. Never invent employers, titles, skills or numbers the candidate "
         "did not give; where a metric would help, use a placeholder like [X%]. Plain ATS-safe text only. "
         "Reply with ONLY valid JSON.")


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "")
GEMINI_BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")
_gemini_pick = []


def ai_key():
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def ai_backend():
    if ai_key():
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _ver(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)-", name)
    return float(m.group(1)) if m else 0.0


def gemini_models():
    """Candidate chain: GEMINI_MODEL alone when set, else newest flash first and lite models as backup."""
    if _gemini_pick:
        return _gemini_pick
    if GEMINI_MODEL:
        _gemini_pick.append(GEMINI_MODEL)
        return _gemini_pick
    try:
        with urllib.request.urlopen(f"{GEMINI_BASE}/models?key={ai_key()}", timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception:
        raise HTTPException(502, "Could not read Google's model list. Try again in a minute")
    names = [m["name"].split("/")[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    skip = ("preview", "exp", "image", "tts", "embedding", "thinking", "gemma", "lyria",
            "nano-banana", "robotics", "computer-use", "deep-research", "antigravity",
            "transcribe", "omni", "argon", "carbon", "hydrogen", "neon")
    usable = [n for n in names if not any(k in n for k in skip)]

    def tier(n):
        if re.fullmatch(r"gemini-[\d.]+-flash", n):
            return 0
        if n == "gemini-flash-latest":
            return 1
        if re.fullmatch(r"gemini-[\d.]+-flash-lite", n):
            return 2
        if n == "gemini-flash-lite-latest":
            return 3
        return 4

    ranked = sorted(usable, key=lambda n: (tier(n), -_ver(n)))
    if not ranked:
        raise HTTPException(502, "This Google key has no text model available")
    chain = [n for n in ranked if tier(n) <= 1][:2] + [n for n in ranked if tier(n) >= 2][:2]
    _gemini_pick.extend(chain or ranked[:3])
    return _gemini_pick


def _gemini_call(model, payload):
    req = urllib.request.Request(
        f"{GEMINI_BASE}/models/{model}:generateContent?key={ai_key()}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def gemini_text(prompt):
    payload = {
        "systemInstruction": {"parts": [{"text": RULES}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2500,
                             "responseMimeType": "application/json"},
    }
    last = ""
    for model in list(gemini_models()):
        for attempt in range(1):
            try:
                data = _gemini_call(model, payload)
                cands = data.get("candidates") or []
                if not cands:
                    raise HTTPException(502, "Google AI returned nothing. Try again")
                parts = (cands[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    raise HTTPException(502, "Google AI returned an empty answer. Try again")
                if model != _gemini_pick[0]:
                    _gemini_pick.insert(0, model)
                return text
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    msg = json.loads(raw)["error"]["message"]
                except Exception:
                    msg = raw
                if e.code in (400, 401, 403) and ("API key" in msg or "API_KEY" in msg or "PERMISSION" in msg):
                    raise HTTPException(500, "Google rejected the AI key. Check GEMINI_API_KEY on the server")
                last = msg
                if e.code in (429, 503) and attempt == 0:
                    time.sleep(1.5)
                    continue
                break
            except HTTPException:
                raise
            except Exception as e:
                last = str(e)[:200]
                break
    if "high demand" in last or "overloaded" in last.lower():
        raise HTTPException(503, "Google's AI is busy right now — try again in a moment")
    raise HTTPException(502, "Google AI error: " + last[:280])


def ask(u, prompt):
    if u["plan"] != "pro" and u["ai_used"] >= FREE_AI:
        raise HTTPException(402, "You've used your free AI edits. Upgrade to Pro for unlimited")
    backend = ai_backend()
    if not backend:
        raise HTTPException(500, "AI is not switched on: add GEMINI_API_KEY to the server")
    if backend == "gemini":
        t = gemini_text(prompt)
    else:
        m = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=2500, system=RULES,
                                                  messages=[{"role": "user", "content": prompt}])
        t = m.content[0].text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        out = json.loads(t)
    except ValueError:
        raise HTTPException(502, "The AI reply came back malformed. Try again")
    q("update users set ai_used=ai_used+1 where id=?", (u["id"],))
    return out


@app.post("/api/ai/rewrite")
def rewrite(b: dict, u=Depends(user)):
    lines = [l.strip(" -•\t") for l in b.get("bullets", "").splitlines() if l.strip()]
    if not lines:
        raise HTTPException(400, "Add at least one bullet first")
    out = ask(u, "Rewrite each bullet for impact: strong action verb, concrete outcome, max 25 words, no first "
                 f"person. Role: {b.get('role', '')} at {b.get('company', '')}. Return "
                 f'{{"bullets": [...]}} with exactly {len(lines)} items.\n\n' + json.dumps(lines))
    return {"bullets": out.get("bullets", lines)}


@app.post("/api/ai/tailor")
def tailor(b: dict, u=Depends(user)):
    d, jd = b.get("data", {}), b.get("jd", "").strip()
    if len(jd) < 50:
        raise HTTPException(400, "Paste the full job description")
    roles = [{"position": e.get("position"), "company": e.get("company"), "bullets": e.get("bullets", "")}
             for e in d.get("experience", [])]
    out = ask(u, "Tailor this resume to the job description: reorder emphasis and mirror its keywords ONLY where "
                 "the candidate truly has that experience. Return {\"summary\": str, \"experience_bullets\": "
                 "[one newline-separated string per role, same order and count], \"missing_keywords\": [str]}."
                 f"\n\nSUMMARY: {d.get('summary', '')}\nROLES: {json.dumps(roles)}\n\nJOB DESCRIPTION:\n{jd}")
    d["summary"] = out.get("summary", d.get("summary", ""))
    for e, nb in zip(d.get("experience", []), out.get("experience_bullets", [])):
        e["bullets"] = nb if isinstance(nb, str) else "\n".join(nb)
    return {"data": d, "missing_keywords": out.get("missing_keywords", [])}


# ---------- Stripe ----------
@app.post("/api/checkout")
def checkout(u=Depends(user)):
    s = stripe.checkout.Session.create(
        mode="subscription", line_items=[{"price": os.environ.get("STRIPE_PRICE_ID", ""), "quantity": 1}],
        customer_email=u["email"], client_reference_id=str(u["id"]),
        success_url=APP_URL + "/?upgraded=1", cancel_url=APP_URL)
    return {"url": s.url}


@app.post("/api/stripe/webhook")
async def hook(req: Request):
    try:
        ev = stripe.Webhook.construct_event(await req.body(), req.headers.get("stripe-signature", ""),
                                            os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
    except Exception:
        raise HTTPException(400, "Bad signature")
    o = ev["data"]["object"]
    if ev["type"] == "checkout.session.completed":
        q("update users set plan='pro',customer=? where id=?", (o.get("customer"), int(o["client_reference_id"])))
    elif ev["type"] == "customer.subscription.deleted":
        q("update users set plan='free' where customer=?", (o["customer"],))
    return {"ok": True}


# ---------- front end (four files, served from the app directory) ----------
BASE = Path(__file__).resolve().parent
FRONT_END = {"": "index.html", "index.html": "index.html", "app.js": "app.js",
             "styles.css": "styles.css", "favicon.svg": "favicon.svg"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(BASE / "index.html", media_type="text/html")


@app.get("/{asset}", include_in_schema=False)
def asset(asset: str):
    name = FRONT_END.get(asset)
    if not name:
        raise HTTPException(404, "Not found")
    return FileResponse(BASE / name)
