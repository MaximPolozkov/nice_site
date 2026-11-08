"""Сайт: публичная страница «Ниши недели» и закрытая панель управления ботом."""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import secrets
import time
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from bot import config, engine, scheduling, storage
from bot.engine import COMPETITION_RU, num, rub
from bot.llm import llm_configured

log = logging.getLogger("web")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)   # за Caddy
if not config.SECRET_KEY:
    log.warning("SECRET_KEY не задан — сгенерирован временный, сессии сбросятся при перезапуске")
app.config.update(
    SECRET_KEY=config.SECRET_KEY or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=7),
)

# --- шаблонные помощники ---
app.jinja_env.globals.update(rub=rub, num=num, COMPETITION_RU=COMPETITION_RU, config=config)


@app.template_filter("dtru")
def dtru(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = dt.datetime.fromisoformat(value)
    except ValueError:
        return value
    return d.strftime("%d.%m.%Y %H:%M") if len(value) > 10 else d.strftime("%d.%m.%Y")


@app.template_filter("parts")
def parts_filter(value: str | None) -> dict:
    try:
        return json.loads(value or "{}")
    except ValueError:
        return {}


@app.before_request
def open_db():
    g.db = storage.connect()


@app.teardown_request
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/admin"):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# --- CSRF и вход ---
def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Форма устарела — обновите страницу")


_failed: dict[str, list[float]] = {}          # IP → время неудачных попыток
MAX_FAILS, WINDOW = 5, 15 * 60


def _too_many(ip: str) -> bool:
    now = time.time()
    _failed[ip] = [t for t in _failed.get(ip, []) if now - t < WINDOW]
    return len(_failed[ip]) >= MAX_FAILS


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    configured = len(config.ADMIN_PASSWORD) >= 10
    if request.method == "POST" and configured:
        ip = request.remote_addr or "?"
        if _too_many(ip):
            flash("Слишком много попыток. Подождите 15 минут.", "error")
        elif hmac.compare_digest(request.form.get("password", "").encode(), config.ADMIN_PASSWORD.encode()):
            session.clear()
            session["admin"] = True
            session.permanent = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("dashboard"))
        else:
            _failed.setdefault(ip, []).append(time.time())
            flash("Неверный пароль", "error")
    return render_template("login.html", configured=configured)


@app.post("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# --- публичная часть ---
@app.get("/")
def index():
    date, items, demo = storage.public_niches(g.db, include_demo=config.DEMO_MODE)
    return render_template("index.html", date=date, items=items, demo=demo)


@app.get("/archive")
def archive():
    rows = storage.public_archive(g.db, include_demo=config.DEMO_MODE)
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["run_date"], []).append(r)
    return render_template("archive.html", grouped=grouped)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- панель ---
def key_status() -> list[tuple[str, bool, str]]:
    return [
        ("Вордстат", bool(config.WORDSTAT_TOKEN), "WORDSTAT_TOKEN"),
        ((f"Нейросеть ({config.LLM_PROVIDER})" if llm_configured()
          else "Нейросеть — не подключена, работает каталог ниш"),
         llm_configured(), "необязательно: LLM_PROVIDER, LLM_API_KEY"),
        ("Поиск цен: SerpAPI", bool(config.SERPAPI_KEY), "SERPAPI_KEY (необязательно)"),
        ("VK: отчёт админам", bool(config.VK_GROUP_TOKEN and config.VK_ADMIN_PEER_IDS),
         "VK_GROUP_TOKEN, VK_ADMIN_PEER_IDS"),
        ("VK: стена", bool(config.VK_USER_TOKEN and config.VK_GROUP_ID), "VK_USER_TOKEN, VK_GROUP_ID"),
    ]


@app.get("/admin")
@login_required
def dashboard():
    now = dt.datetime.now(ZoneInfo(config.TZ))
    return render_template(
        "dashboard.html",
        running=storage.is_running(g.db),
        pending=storage.has_pending_request(g.db),
        runs=storage.runs(g.db, 15),
        has_demo=g.db.execute("SELECT 1 FROM runs WHERE demo=1").fetchone() is not None,
        totals=storage.totals(g.db),
        keys=key_status(),
        schedule=scheduling.describe() if config.SCHEDULE_ENABLED else "выключено",
        next_run=scheduling.next_run(now) if config.SCHEDULE_ENABLED else None,
    )


@app.post("/admin/run")
@login_required
def start_run():
    if storage.is_running(g.db) or storage.has_pending_request(g.db):
        flash("Бот уже работает — дождитесь окончания.", "error")
    else:
        storage.request_run(g.db, publish=request.form.get("publish") == "1")
        flash("Запуск поставлен в очередь, начнётся в течение 15 секунд. Прогон занимает несколько минут.", "ok")
    return redirect(url_for("dashboard"))


@app.get("/admin/runs/<int:run_id>")
@login_required
def run_detail(run_id: int):
    r = storage.get_run(g.db, run_id)
    if not r:
        abort(404)
    return render_template("run.html", run=r, items=storage.forecasts_for_run(g.db, run_id))


@app.post("/admin/runs/<int:run_id>/republish")
@login_required
def republish(run_id: int):
    ok = engine.republish(run_id)
    flash("Отчёт отправлен в VK." if ok else "Не удалось отправить в VK — смотрите журнал запуска и ключи.",
          "ok" if ok else "error")
    return redirect(url_for("run_detail", run_id=run_id))


@app.post("/admin/forecasts/<int:fid>/toggle")
@login_required
def toggle(fid: int):
    storage.toggle_public(g.db, fid)
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/admin/demo/delete")
@login_required
def delete_demo():
    n = storage.delete_demo(g.db)
    flash(f"Удалено демо-запусков: {n}", "ok")
    return redirect(url_for("dashboard"))


@app.get("/admin/results")
@login_required
def results():
    return render_template("results.html", rows=storage.forecast_vs_fact(g.db),
                           outcomes=storage.outcomes(g.db), niches=storage.known_niches(g.db),
                           totals=storage.totals(g.db))


def _int(name: str) -> int:
    try:
        return max(0, int(request.form.get(name, "0").replace(" ", "") or 0))
    except ValueError:
        return 0


@app.post("/admin/outcomes")
@login_required
def add_outcome():
    niche = request.form.get("niche", "").strip()[:200]
    if not niche:
        flash("Укажите нишу", "error")
    else:
        storage.add_outcome(g.db, niche, _int("leads"), _int("deals"), _int("commission"),
                            request.form.get("comment", "").strip()[:500])
        flash("Результат записан", "ok")
    return redirect(url_for("results"))


@app.post("/admin/outcomes/<int:oid>/delete")
@login_required
def delete_outcome(oid: int):
    storage.delete_outcome(g.db, oid)
    flash("Запись удалена", "ok")
    return redirect(url_for("results"))


@app.errorhandler(400)
@app.errorhandler(404)
def error_page(e):
    return render_template("error.html", error=e), e.code
