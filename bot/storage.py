"""База: прогнозы, запуски бота, заявки на запуск и фактические результаты (SQLite)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    status TEXT NOT NULL DEFAULT 'running',     -- running | ok | error
    trigger TEXT,                               -- schedule | manual
    demo INTEGER DEFAULT 0,
    published INTEGER DEFAULT 0,
    post_text TEXT,
    log TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS run_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    publish INTEGER DEFAULT 1,
    taken INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    run_date TEXT NOT NULL,
    niche TEXT NOT NULL,
    category TEXT,
    phrase TEXT,
    who_pays TEXT,
    volume INTEGER,
    yoy_pct REAL,
    season TEXT,
    check_min INTEGER,
    check_max INTEGER,
    commission_pct REAL,
    competition INTEGER,
    confidence REAL,
    note TEXT,
    score REAL,
    parts TEXT,
    in_top INTEGER DEFAULT 0,       -- попала в топ запуска
    published INTEGER DEFAULT 0,    -- отправлена в VK
    public INTEGER DEFAULT 0        -- видна на публичной странице
);
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    niche TEXT NOT NULL,
    leads INTEGER DEFAULT 0,
    deals INTEGER DEFAULT 0,
    commission_rub INTEGER DEFAULT 0,
    comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_forecasts_run ON forecasts(run_id);
"""


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")      # сайт и worker пишут одновременно
    con.executescript(SCHEMA)
    return con


# --- запуски ---
def start_run(con, trigger: str, demo: bool) -> int:
    cur = con.execute("INSERT INTO runs (started, trigger, demo) VALUES (?,?,?)", (now(), trigger, int(demo)))
    con.commit()
    return cur.lastrowid


def append_log(con, run_id: int, line: str) -> None:
    con.execute("UPDATE runs SET log = log || ? WHERE id=?", (line + "\n", run_id))
    con.commit()


def finish_run(con, run_id: int, status: str, post_text: str | None = None, published: bool = False) -> None:
    con.execute("UPDATE runs SET finished=?, status=?, post_text=?, published=? WHERE id=?",
                (now(), status, post_text, int(published), run_id))
    con.commit()


def is_running(con) -> bool:
    return con.execute("SELECT 1 FROM runs WHERE status='running'").fetchone() is not None


def fail_stale_runs(con) -> None:
    """Если worker перезапустился посреди прогона — помечаем зависший запуск."""
    con.execute("UPDATE runs SET status='error', finished=?, log = log || 'Прервано перезапуском\n' "
                "WHERE status='running'", (now(),))
    con.commit()


def runs(con, limit: int = 30):
    return con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_run(con, run_id: int):
    return con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def last_scheduled_run_date(con) -> str | None:
    row = con.execute("SELECT MAX(started) m FROM runs WHERE trigger='schedule'").fetchone()
    return row["m"][:10] if row and row["m"] else None


# --- заявки на ручной запуск (сайт → worker) ---
def request_run(con, publish: bool) -> None:
    con.execute("INSERT INTO run_requests (created, publish) VALUES (?,?)", (now(), int(publish)))
    con.commit()


def take_request(con):
    row = con.execute("SELECT * FROM run_requests WHERE taken=0 ORDER BY id LIMIT 1").fetchone()
    if row:
        con.execute("UPDATE run_requests SET taken=1 WHERE id=?", (row["id"],))
        con.commit()
    return row


def has_pending_request(con) -> bool:
    return con.execute("SELECT 1 FROM run_requests WHERE taken=0").fetchone() is not None


# --- прогнозы ---
def save_forecast(con, run_id: int, n, season: str, in_top: bool, published: bool) -> None:
    con.execute(
        """INSERT INTO forecasts (run_id, run_date, niche, category, phrase, who_pays, volume, yoy_pct,
           season, check_min, check_max, commission_pct, competition, confidence, note, score, parts,
           in_top, published, public) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, dt.date.today().isoformat(), n.niche, n.category, n.phrase, n.who_pays, n.volume,
         n.yoy_pct, season, n.check_min, n.check_max, n.commission_pct, n.competition, n.confidence,
         n.note, n.total, json.dumps(n.parts, ensure_ascii=False), int(in_top), int(published),
         int(in_top)))
    con.commit()


def mark_published(con, run_id: int) -> None:
    con.execute("UPDATE forecasts SET published=1 WHERE run_id=? AND in_top=1", (run_id,))
    con.commit()


def forecasts_for_run(con, run_id: int):
    return con.execute("SELECT * FROM forecasts WHERE run_id=? ORDER BY score DESC", (run_id,)).fetchall()


def latest_run_with_forecasts(con):
    return con.execute("""SELECT r.* FROM runs r WHERE r.status='ok'
                          AND EXISTS (SELECT 1 FROM forecasts f WHERE f.run_id=r.id)
                          ORDER BY r.id DESC LIMIT 1""").fetchone()


def toggle_public(con, forecast_id: int) -> None:
    con.execute("UPDATE forecasts SET public = 1 - public WHERE id=?", (forecast_id,))
    con.commit()


def _demo_filter(include_demo: bool) -> str:
    return "" if include_demo else " AND run_id IN (SELECT id FROM runs WHERE demo=0)"


def public_niches(con, include_demo: bool = False):
    """Публичная страница: видимые ниши из последнего запуска, где такие есть.

    Возвращает (дата, ниши, демо ли это). Демо-запуски показываются только в демо-режиме.
    """
    row = con.execute("SELECT MAX(run_id) r FROM forecasts WHERE public=1" + _demo_filter(include_demo)).fetchone()
    if not row or row["r"] is None:
        return None, [], False
    items = con.execute("SELECT * FROM forecasts WHERE run_id=? AND public=1 ORDER BY score DESC",
                        (row["r"],)).fetchall()
    run = get_run(con, row["r"])
    return items[0]["run_date"], items, bool(run and run["demo"])


def public_archive(con, limit_runs: int = 12, include_demo: bool = False):
    flt = _demo_filter(include_demo)
    return con.execute(f"""SELECT run_date, niche, score FROM forecasts WHERE public=1 {flt} AND run_id IN
                          (SELECT DISTINCT run_id FROM forecasts WHERE public=1 {flt} ORDER BY run_id DESC LIMIT ?)
                          ORDER BY run_id DESC, score DESC""", (limit_runs,)).fetchall()


def delete_demo(con) -> int:
    """Удаляет демо-запуски и их ниши — перед переходом в боевой режим."""
    ids = [r["id"] for r in con.execute("SELECT id FROM runs WHERE demo=1")]
    con.executemany("DELETE FROM forecasts WHERE run_id=?", [(i,) for i in ids])
    con.executemany("DELETE FROM runs WHERE id=?", [(i,) for i in ids])
    con.commit()
    return len(ids)


def recently_published(con, weeks: int) -> list[str]:
    since = (dt.date.today() - dt.timedelta(weeks=weeks)).isoformat()
    rows = con.execute("SELECT DISTINCT niche FROM forecasts WHERE in_top=1 AND run_date>=?"
                       " AND run_id IN (SELECT id FROM runs WHERE demo=0)", (since,))
    return [r["niche"] for r in rows]


# --- результаты ---
def add_outcome(con, niche, leads, deals, commission, comment="") -> None:
    con.execute("INSERT INTO outcomes (date, niche, leads, deals, commission_rub, comment) VALUES (?,?,?,?,?,?)",
                (dt.date.today().isoformat(), niche, leads, deals, commission, comment))
    con.commit()


def outcomes(con, limit: int = 50):
    return con.execute("SELECT * FROM outcomes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def delete_outcome(con, outcome_id: int) -> None:
    con.execute("DELETE FROM outcomes WHERE id=?", (outcome_id,))
    con.commit()


def known_niches(con) -> list[str]:
    return [r["niche"] for r in con.execute(
        "SELECT niche FROM forecasts WHERE in_top=1 GROUP BY niche ORDER BY MAX(id) DESC")]


def forecast_vs_fact(con):
    return con.execute("""
        WITH f AS (SELECT niche, MAX(score) best_score, MIN(run_date) first_seen
                   FROM forecasts WHERE in_top=1 GROUP BY niche),
             o AS (SELECT niche, SUM(leads) leads, SUM(deals) deals, SUM(commission_rub) commission
                   FROM outcomes GROUP BY niche)
        SELECT COALESCE(f.niche, o.niche) niche, f.best_score, f.first_seen,
               COALESCE(o.leads,0) leads, COALESCE(o.deals,0) deals, COALESCE(o.commission,0) commission
        FROM f LEFT JOIN o ON o.niche=f.niche
        UNION
        SELECT o.niche, NULL, NULL, o.leads, o.deals, o.commission
        FROM o WHERE o.niche NOT IN (SELECT niche FROM f)
        ORDER BY commission DESC, best_score DESC
    """).fetchall()


def totals(con) -> dict:
    row = con.execute("SELECT COALESCE(SUM(leads),0) l, COALESCE(SUM(deals),0) d, "
                      "COALESCE(SUM(commission_rub),0) c FROM outcomes").fetchone()
    return {"leads": row["l"], "deals": row["d"], "commission": row["c"]}
