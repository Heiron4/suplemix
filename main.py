"""SupleMix backend: account + onboarding (FastAPI).
Run:  pip install -r requirements.txt && uvicorn main:app --reload
Open: http://localhost:8000
Database: SQLite by default; set DATABASE_URL=postgresql+psycopg2://user:pw@192.168.1.2:5432/suplemix for PostgreSQL.
"""
import os, datetime as dt
import bcrypt, jwt
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
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

@app.post("/api/auth/login")
def login(d: Login, s: Session = Depends(db)):
    u = s.scalar(select(User).where((User.username == d.identifier) | (User.email == d.identifier.lower())))
    if not u or not bcrypt.checkpw(d.password.encode(), u.password.encode()):
        raise HTTPException(401, "Wrong username or password.")
    return {"token": token_for(u), "user": public(u)}

@app.get("/api/me")
def me(u: User = Depends(current_user), s: Session = Depends(db)):
    t = s.scalar(select(NutritionTarget).where(NutritionTarget.user_id == u.user_id))
    return {**public(u), "gender": u.gender, "weight": u.weight, "height": u.height,
            "birth_date": u.birth_date, "workout_routine": u.workout_routine, "goal": u.goal,
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

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True))
