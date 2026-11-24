"""SupleMix backend: account + onboarding (FastAPI).
Run:  pip install -r requirements.txt && uvicorn main:app --reload
Open: http://localhost:8000
Database: SQLite by default; set DATABASE_URL=postgresql+psycopg2://user:pw@192.168.1.2:5432/suplemix for PostgreSQL.
"""
import os, math, time, datetime as dt
import re, bcrypt, jwt
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from fastapi import Request
from sqlalchemy import create_engine, String, Float, Date, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

SECRET = os.getenv("SECRET_KEY", "change-me-in-production")
DB_URL = os.getenv("DATABASE_URL", "sqlite:///suplemix.db")
if DB_URL.startswith(("postgres://", "postgresql://")):      # Render gives postgres://
    DB_URL = "postgresql+psycopg2://" + DB_URL.split("://", 1)[1]
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").lower()           # this email becomes admin when it registers
engine = create_engine(DB_URL)
SessionLocal = sessionmaker(engine)

class Base(DeclarativeBase): pass

class User(Base):                      # tbl_user
    __tablename__ = "tbl_user"
    user_id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(500), unique=True)
    password: Mapped[str] = mapped_column(String(100))   # bcrypt hash is 60 chars, so the dictionary's Varchar(8) must grow
    email: Mapped[str] = mapped_column(String(500), unique=True)
    role: Mapped[str] = mapped_column(String(10), default="user")  # RBAC: user / admin
    gender: Mapped[str | None] = mapped_column(String(6))
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    birth_date: Mapped[dt.date | None] = mapped_column(Date)
    workout_routine: Mapped[str | None] = mapped_column(String(500))
    goal: Mapped[str | None] = mapped_column(String(100))

class NutritionTarget(Base):           # NutritionTarget_tbl (Fat added: the UI shows it)
    __tablename__ = "nutrition_target_tbl"
    nutrition_target_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tbl_user.user_id"), unique=True)
    calories: Mapped[float] = mapped_column(Float)
    protein: Mapped[float] = mapped_column(Float)
    carbs: Mapped[float] = mapped_column(Float)
    fat: Mapped[float] = mapped_column(Float)

class NutritionLog(Base):              # NutritionLog_tbl
    __tablename__ = "nutrition_log_tbl"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tbl_user.user_id"), index=True)
    meal_name: Mapped[str] = mapped_column(String(200))
    log_date: Mapped[dt.date] = mapped_column(Date)
    log_time: Mapped[str] = mapped_column(String(5))
    calories: Mapped[float] = mapped_column(Float, default=0.0)
    protein: Mapped[float] = mapped_column(Float, default=0.0)
    carbs: Mapped[float] = mapped_column(Float, default=0.0)
    fat: Mapped[float] = mapped_column(Float, default=0.0)

class Mix(Base):                       # mix_tbl (scheduled supplement mixes)
    __tablename__ = "mix_tbl"
    mix_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tbl_user.user_id"), index=True)
    mix_name: Mapped[str] = mapped_column(String(200))   # the dictionary says int8, but a name must be text
    mix_date: Mapped[dt.date] = mapped_column(Date)
    mix_time: Mapped[str] = mapped_column(String(5))
    whey_protein: Mapped[float] = mapped_column(Float, default=0.0)   # grams, max 60
    creatine: Mapped[float] = mapped_column(Float, default=0.0)       # grams, max 10
    preworkout: Mapped[float] = mapped_column(Float, default=0.0)     # grams, max 10
    auto_dispense: Mapped[bool] = mapped_column(default=True)

Base.metadata.create_all(engine)

def db():
    with SessionLocal() as s: yield s

# ---------- TDEE and targets (Mifflin-St Jeor, section 3.4.1) ----------
ACTIVITY = {"Sedentary": 1.2, "Lightly Active": 1.375, "Moderately Active": 1.55, "Very Active": 1.725}
GOALS = {  # kcal adjustment, protein g per kg
    "Lose weight": (-500, 1.8), "Lose weight + build muscle": (-300, 2.2),
    "Maintain fitness": (0, 1.6), "Gain weight": (400, 1.6), "Gain weight + build muscle": (250, 2.0)}

def age_of(b: dt.date) -> int:
    t = dt.date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))

def compute_targets(u: User) -> dict:
    s = 5 if u.gender == "Male" else -161
    bmr = 10 * u.weight + 6.25 * u.height - 5 * age_of(u.birth_date) + s
    tdee = bmr * ACTIVITY[u.workout_routine]
    adj, per_kg = GOALS[u.goal]
    kcal = min(max(tdee + adj, 1200), 4500)             # capped at safe limits
    protein = round(u.weight * per_kg)
    fat = round(kcal * 0.25 / 9)
    carbs = round(max(kcal - protein * 4 - fat * 9, 0) / 4)
    return {"tdee": round(tdee), "calories": round(kcal), "protein": protein, "carbs": carbs, "fat": fat}

# ---------- Auth ----------
bearer = HTTPBearer(auto_error=False)

def token_for(u: User) -> str:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)
    return jwt.encode({"sub": str(u.user_id), "role": u.role, "exp": exp}, SECRET, "HS256")

def current_user(c: HTTPAuthorizationCredentials | None = Depends(bearer), s: Session = Depends(db)) -> User:
    try:
        uid = int(jwt.decode(c.credentials, SECRET, ["HS256"])["sub"])
    except Exception:
        raise HTTPException(401, "Your session expired. Log in again.")
    u = s.get(User, uid)
    if not u: raise HTTPException(401, "Account not found. Log in again.")
    return u

def require_admin(u: User = Depends(current_user)) -> User:
    if u.role != "admin": raise HTTPException(403, "Admins only.")
    return u

# ---------- Schemas ----------
class Register(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.]+$")
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def strong(cls, v):
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)): raise ValueError("Password needs a letter and a number.")
        if len(v.encode()) > 72: raise ValueError("Password is too long.")
        return v

class Login(BaseModel):
    identifier: str   # username or email
    password: str

class Onboarding(BaseModel):
    gender: str = Field(pattern="^(Male|Female)$")
    weight: float = Field(ge=30, le=350)      # kg
    height: float = Field(ge=100, le=250)     # cm
    birth_date: dt.date
    workout_routine: str
    goal: str

app = FastAPI(title="SupleMix API")

def public(u: User) -> dict:
    return {"user_id": u.user_id, "username": u.username, "email": u.email, "role": u.role,
            "onboarded": u.birth_date is not None}

@app.post("/api/auth/register", status_code=201)
def register(d: Register, s: Session = Depends(db)):
    if s.scalar(select(User).where((User.username == d.username) | (User.email == d.email.lower()))):
        raise HTTPException(409, "That username or email is already registered.")
    u = User(username=d.username, email=d.email.lower(),
             password=bcrypt.hashpw(d.password.encode(), bcrypt.gensalt()).decode(),
             role="admin" if d.email.lower() == ADMIN_EMAIL else "user")
    s.add(u); s.commit(); s.refresh(u)
    return {"token": token_for(u), "user": public(u)}

FAILS: dict = {}                                  # brute-force protection: 5 misses = 5 min lock
DUMMY = bcrypt.hashpw(b"x", bcrypt.gensalt()).decode()

@app.post("/api/auth/login")
def login(d: Login, req: Request, s: Session = Depends(db)):
    key = f"{req.client.host if req.client else '?'}|{d.identifier.lower()[:100]}"
    n, until = FAILS.get(key, (0, 0))
    if until > time.time(): raise HTTPException(429, f"Too many attempts. Try again in {int(until - time.time()) // 60 + 1} min.")
    u = s.scalar(select(User).where((User.username == d.identifier) | (User.email == d.identifier.lower())))
    ok = bcrypt.checkpw(d.password.encode()[:72], (u.password if u else DUMMY).encode())   # same work either way
    if not (u and ok):
        FAILS[key] = (0, time.time() + 300) if n + 1 >= 5 else (n + 1, 0)
        raise HTTPException(401, "Wrong username or password.")
    FAILS.pop(key, None)
    return {"token": token_for(u), "user": public(u)}

@app.get("/api/me")
def me(u: User = Depends(current_user), s: Session = Depends(db)):
    t = s.scalar(select(NutritionTarget).where(NutritionTarget.user_id == u.user_id))
    return {**public(u), "gender": u.gender, "weight": u.weight, "height": u.height,
            "birth_date": u.birth_date, "workout_routine": u.workout_routine, "goal": u.goal,
            "tdee": compute_targets(u)["tdee"] if u.birth_date and u.workout_routine in ACTIVITY and u.goal in GOALS else None,
            "targets": t and {"calories": t.calories, "protein": t.protein, "carbs": t.carbs, "fat": t.fat}}

@app.put("/api/me/onboarding")
def onboarding(d: Onboarding, u: User = Depends(current_user), s: Session = Depends(db)):
    if d.workout_routine not in ACTIVITY: raise HTTPException(422, "Pick one of the workout options.")
    if d.goal not in GOALS: raise HTTPException(422, "Pick one of the goals.")
    if not 13 <= age_of(d.birth_date) <= 100: raise HTTPException(422, "Enter a valid date of birth (age 13 to 100).")
    u.gender, u.weight, u.height, u.birth_date = d.gender, d.weight, d.height, d.birth_date
    u.workout_routine, u.goal = d.workout_routine, d.goal
    t = compute_targets(u)
    row = s.scalar(select(NutritionTarget).where(NutritionTarget.user_id == u.user_id)) or NutritionTarget(user_id=u.user_id)
    row.calories, row.protein, row.carbs, row.fat = t["calories"], t["protein"], t["carbs"], t["fat"]
    s.add(row); s.commit()
    return t

@app.get("/api/admin/users")          # first admin endpoint, to be expanded in the admin module
def list_users(_: User = Depends(require_admin), s: Session = Depends(db)):
    return [public(u) for u in s.scalars(select(User))]

class LogIn(BaseModel):
    meal_name: str = Field(min_length=1, max_length=200)
    day: dt.date
    log_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    calories: float = Field(ge=0, le=5000)      # intake cannot be negative (3.4.2)
    protein: float = Field(ge=0, le=500)
    carbs: float = Field(ge=0, le=1000)
    fat: float = Field(ge=0, le=500)

def day_logs(s: Session, uid: int, day: dt.date) -> dict:
    rows = s.scalars(select(NutritionLog).where(NutritionLog.user_id == uid, NutritionLog.log_date == day)
                     .order_by(NutritionLog.log_time)).all()
    items = [{k: getattr(r, k) for k in ("id", "meal_name", "log_time", "calories", "protein", "carbs", "fat")} for r in rows]
    return {"items": items, "totals": {k: sum(i[k] for i in items) for k in ("calories", "protein", "carbs", "fat")}}

@app.get("/api/logs")
def get_logs(day: dt.date, u: User = Depends(current_user), s: Session = Depends(db)):
    return day_logs(s, u.user_id, day)

@app.post("/api/logs", status_code=201)
def add_log(d: LogIn, u: User = Depends(current_user), s: Session = Depends(db)):
    s.add(NutritionLog(user_id=u.user_id, meal_name=d.meal_name, log_date=d.day, log_time=d.log_time,
                       calories=d.calories, protein=d.protein, carbs=d.carbs, fat=d.fat))
    s.commit(); return {"ok": True}

@app.delete("/api/logs/{log_id}")
def del_log(log_id: int, u: User = Depends(current_user), s: Session = Depends(db)):
    r = s.get(NutritionLog, log_id)
    if not r or r.user_id != u.user_id: raise HTTPException(404, "Meal not found.")
    s.delete(r); s.commit(); return {"ok": True}

@app.get("/api/admin/users/{uid}")    # admin monitors only: read-only view of a customer
def admin_user(uid: int, _: User = Depends(require_admin), s: Session = Depends(db)):
    u = s.get(User, uid)
    if not u: raise HTTPException(404, "Customer not found.")
    t = s.scalar(select(NutritionTarget).where(NutritionTarget.user_id == uid))
    return {**public(u), "gender": u.gender, "weight": u.weight, "height": u.height, "birth_date": u.birth_date,
            "workout_routine": u.workout_routine, "goal": u.goal,
            "targets": t and {"calories": t.calories, "protein": t.protein, "carbs": t.carbs, "fat": t.fat},
            "today": day_logs(s, uid, dt.date.today())}

# ---------- Security headers ----------
@app.middleware("http")
async def secure_headers(req: Request, call_next):
    r = await call_next(req)
    r.headers.update({
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                   "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://world.openfoodfacts.org; "
                                   "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
    if req.url.path.startswith(("/docs", "/redoc")): del r.headers["Content-Security-Policy"]   # Swagger UI needs its CDN
    if req.url.path.startswith("/api/"): r.headers["Cache-Control"] = "no-store"
    return r

if SECRET == "change-me-in-production": print("WARNING: set SECRET_KEY before deploying.")

# ---------- Device + dispensing log (simulated until the ESP32 is connected) ----------
class Device(Base):
    __tablename__ = "device_tbl"
    user_id: Mapped[int] = mapped_column(ForeignKey("tbl_user.user_id"), primary_key=True)
    whey_level: Mapped[float] = mapped_column(Float, default=80.0)
    creatine_level: Mapped[float] = mapped_column(Float, default=65.0)
    preworkout_level: Mapped[float] = mapped_column(Float, default=70.0)
    online: Mapped[bool] = mapped_column(default=True)

class DispenseLog(Base):
    __tablename__ = "dispense_log_tbl"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("tbl_user.user_id"), index=True)
    mix_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))

Base.metadata.create_all(engine)
CAP = {"whey_level": 1000, "creatine_level": 300, "preworkout_level": 300}   # grams per container
TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
mins = lambda t: int(t[:2]) * 60 + int(t[3:])

def get_dev(s: Session, uid: int) -> Device:
    d = s.get(Device, uid)
    if not d: d = Device(user_id=uid); s.add(d); s.commit(); s.refresh(d)
    return d

# ---------- Mixes (3.4.3: safety limits + 15-minute collision rule) ----------
class MixIn(BaseModel):
    mix_name: str = Field(min_length=1, max_length=200)
    mix_date: dt.date
    mix_time: str = Field(pattern=TIME)
    whey_protein: float = Field(ge=0, le=60)
    creatine: float = Field(ge=0, le=10)
    preworkout: float = Field(ge=0, le=10)
    auto_dispense: bool = True

MIX_KEYS = ("mix_id", "mix_name", "mix_date", "mix_time", "whey_protein", "creatine", "preworkout", "auto_dispense")

@app.get("/api/mixes")
def get_mixes(u: User = Depends(current_user), s: Session = Depends(db)):
    rows = s.scalars(select(Mix).where(Mix.user_id == u.user_id).order_by(Mix.mix_date, Mix.mix_time)).all()
    return [{k: getattr(r, k) for k in MIX_KEYS} for r in rows]

@app.post("/api/mixes", status_code=201)
def add_mix(d: MixIn, u: User = Depends(current_user), s: Session = Depends(db)):
    if d.whey_protein + d.creatine + d.preworkout <= 0: raise HTTPException(422, "Add at least one ingredient.")
    if d.auto_dispense:
        same = s.scalars(select(Mix).where(Mix.user_id == u.user_id, Mix.mix_date == d.mix_date, Mix.auto_dispense == True)).all()  # noqa: E712
        if any(abs(mins(m.mix_time) - mins(d.mix_time)) < 15 for m in same):
            raise HTTPException(409, "Another automatic mix is scheduled within 15 minutes of that time.")
    s.add(Mix(user_id=u.user_id, **d.model_dump())); s.commit(); return {"ok": True}

@app.delete("/api/mixes/{mix_id}")
def del_mix(mix_id: int, u: User = Depends(current_user), s: Session = Depends(db)):
    m = s.get(Mix, mix_id)
    if not m or m.user_id != u.user_id: raise HTTPException(404, "Mix not found.")
    s.delete(m); s.commit(); return {"ok": True}

# ---------- Device endpoints (3.4.4 ready checks) ----------
LV = ("whey_level", "creatine_level", "preworkout_level", "online")

@app.get("/api/device")
def device(u: User = Depends(current_user), s: Session = Depends(db)):
    d = get_dev(s, u.user_id); return {k: getattr(d, k) for k in LV}

@app.post("/api/device/refill")
def refill(u: User = Depends(current_user), s: Session = Depends(db)):
    d = get_dev(s, u.user_id); d.whey_level = d.creatine_level = d.preworkout_level = 100.0; s.commit(); return {"ok": True}

class Online(BaseModel): online: bool

@app.put("/api/device/online")
def set_online(b: Online, u: User = Depends(current_user), s: Session = Depends(db)):
    get_dev(s, u.user_id).online = b.online; s.commit(); return {"ok": True}

class Dispense(BaseModel): mix_id: int

@app.post("/api/device/dispense")
def dispense(b: Dispense, u: User = Depends(current_user), s: Session = Depends(db)):
    m = s.get(Mix, b.mix_id)
    if not m or m.user_id != u.user_id: raise HTTPException(404, "Mix not found.")
    d = get_dev(s, u.user_id)
    need = {"whey_level": m.whey_protein, "creatine_level": m.creatine, "preworkout_level": m.preworkout}
    fail = ("The device is offline." if not d.online else
            "Stock is below 10% for an ingredient in this mix." if any(g > 0 and getattr(d, k) < 10 for k, g in need.items()) else
            "Not enough powder left for this mix." if any(getattr(d, k) - g / CAP[k] * 100 < 0 for k, g in need.items()) else None)
    s.add(DispenseLog(user_id=u.user_id, mix_name=m.mix_name, status="failed" if fail else "success", message=fail or ""))
    if not fail:
        for k, g in need.items(): setattr(d, k, round(getattr(d, k) - g / CAP[k] * 100, 1))
    s.commit()
    if fail: raise HTTPException(409, fail)
    return {"ok": True}

@app.get("/api/device/logs")
def device_logs(u: User = Depends(current_user), s: Session = Depends(db)):
    rows = s.scalars(select(DispenseLog).where(DispenseLog.user_id == u.user_id).order_by(DispenseLog.id.desc()).limit(10)).all()
    return [{"mix_name": r.mix_name, "status": r.status, "message": r.message, "created_at": r.created_at.isoformat()} for r in rows]

# ---------- Suggestion, alerts, report, food search ----------
@app.get("/api/suggestion")
def suggestion(day: dt.date, u: User = Depends(current_user), s: Session = Depends(db)):
    t = s.scalar(select(NutritionTarget).where(NutritionTarget.user_id == u.user_id)); l = day_logs(s, u.user_id, day)
    if not t or not l["items"]: return {"needed": False}
    short = t.protein - l["totals"]["protein"]
    if short < 10: return {"needed": False}
    whey = min(60, math.ceil(short / 0.8))          # whey is roughly 80% protein by weight
    return {"needed": True, "whey": whey, "message": f"You are {round(short)} g short of your protein target. About {whey} g of whey would close the gap. This is only a suggestion; nothing is dispensed until you confirm."}

@app.get("/api/alerts")
def alerts(day: dt.date, u: User = Depends(current_user), s: Session = Depends(db)):
    d, out = get_dev(s, u.user_id), []
    if not d.online: out.append("Your dispenser is offline. Only its physical buttons work.")
    for name, k in (("Whey protein", "whey_level"), ("Creatine", "creatine_level"), ("Pre-workout", "preworkout_level")):
        if getattr(d, k) < 20: out.append(f"{name} is running low ({round(getattr(d, k))}%).")
    n = len(s.scalars(select(Mix).where(Mix.user_id == u.user_id, Mix.mix_date == day, Mix.auto_dispense == True)).all())  # noqa: E712
    if n: out.append(f"{n} automatic mix{'es' if n > 1 else ''} scheduled today.")
    return [{"text": t} for t in out]

@app.get("/api/report")
def report(end: dt.date, days: int = 7, u: User = Depends(current_user), s: Session = Depends(db)):
    days = max(1, min(days, 31)); out = []
    for i in range(days):
        day = end - dt.timedelta(days=days - 1 - i); l = day_logs(s, u.user_id, day)
        out.append({"day": day.isoformat(), **l["totals"], "meals": len(l["items"])})
    return out

FOODS = [("White rice, cooked", "1 cup", 205, 4.3, 45, .4), ("Chicken breast, grilled", "100 g", 165, 31, 0, 3.6), ("Egg, boiled", "1 large", 78, 6.3, .6, 5.3),
         ("Banana", "1 medium", 105, 1.3, 27, .4), ("Oatmeal, cooked", "1 cup", 154, 6, 27, 2.6), ("Whole milk", "1 cup", 149, 8, 12, 8),
         ("Whey protein shake", "1 scoop (30 g)", 120, 24, 3, 1.5), ("Bread, whole wheat", "1 slice", 81, 4, 14, 1.1), ("Tuna, canned", "100 g", 116, 26, 0, .8),
         ("Sweet potato, baked", "1 medium", 112, 2, 26, .1), ("Apple", "1 medium", 95, .5, 25, .3), ("Peanut butter", "1 tbsp", 94, 4, 3, 8),
         ("Salmon, baked", "100 g", 206, 22, 0, 12), ("Greek yogurt, plain", "1 cup", 130, 23, 9, 0.7), ("Adobo, chicken", "1 cup", 290, 26, 6, 17)]

@app.get("/api/foods")
def foods(q: str = "", _: User = Depends(current_user)):
    q = q.strip().lower()[:50]
    return [dict(zip(("name", "serving", "calories", "protein", "carbs", "fat"), f)) for f in FOODS if q and q in f[0].lower()][:8]

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True))
