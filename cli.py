"""Командная строка (внутри Docker: docker compose exec worker python cli.py …).

  python cli.py run [--demo] [--no-publish]
  python cli.py outcome "Ниша" --leads 5 --deals 1 --commission 30000
  python cli.py stats
  python cli.py backup          копия базы в /data/backups/
"""
import argparse
import datetime as dt
import logging
import sqlite3
import sys

from bot import config, engine, storage


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--demo", action="store_true")
    r.add_argument("--no-publish", action="store_true")
    o = sub.add_parser("outcome")
    o.add_argument("niche")
    o.add_argument("--leads", type=int, default=0)
    o.add_argument("--deals", type=int, default=0)
    o.add_argument("--commission", type=int, default=0)
    o.add_argument("--comment", default="")
    sub.add_parser("stats")
    sub.add_parser("backup")
    a = ap.parse_args()

    if a.cmd == "backup":
        dest_dir = config.DATA_DIR / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"niches-{dt.datetime.now():%Y%m%d-%H%M}.db"
        with storage.connect() as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
        print(dest)
        return 0

    if a.cmd == "run":
        run_id = engine.run("manual", publish=not a.no_publish, demo=True if a.demo else None)
        con = storage.connect()
        r_ = storage.get_run(con, run_id)
        print(r_["post_text"] or r_["log"])
        return 0 if r_["status"] == "ok" else 1
    con = storage.connect()
    if a.cmd == "outcome":
        storage.add_outcome(con, a.niche, a.leads, a.deals, a.commission, a.comment)
        print("Записано")
        return 0
    for row in storage.forecast_vs_fact(con):
        print(f"{row['niche'][:42]:42} {row['best_score'] or '—':>5} {row['leads']:>5} "
              f"{row['deals']:>5} {engine.rub(row['commission']):>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
