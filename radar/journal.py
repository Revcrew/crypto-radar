"""Paper journal (TZ 2): SQLite + flat CSV exports.

Tables: alerts (CEX), dex_alerts, news_alerts.

Honesty rules (TZ 2.1):
- A horizon is filled only inside [deadline, deadline + max(180s, 5% of deadline)].
  A missed deadline stays NULL forever — never backfilled with a later price.
- Path extrema (min60/max60, min1440/max1440) are tracked continuously from
  the poll feed while the alert is open, seeded with the alert price.
- cohort stamps the collection config version.
"""
from __future__ import annotations

import csv
import logging
import os
import sqlite3

log = logging.getLogger("radar.journal")

# (column, offset_seconds)
HORIZONS: tuple[tuple[str, int], ...] = (
    ("pe30", 30),
    ("pe60", 60),
    ("p1", 60),
    ("p5", 300),
    ("p10", 600),
    ("p15", 900),
    ("p30", 1800),
    ("p60", 3600),
    ("p240", 14400),
    ("p480", 28800),
    ("p720", 43200),
    ("p1440", 86400),
)

# (min_col, max_col, tracking_window_s)
EXTREMA: tuple[tuple[str, str, int], ...] = (
    ("min60", "max60", 3600),
    ("min1440", "max1440", 86400),
)

HORIZON_COLS = [c for c, _ in HORIZONS]
EXTREMA_COLS = [c for pair in EXTREMA for c in pair[:2]]

OPEN_S = 96000.0  # alerts older than this need no more updates
CSV_WRITE_MIN_S = 60.0

_SCHEMA = {
    "alerts": (
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts REAL", "asset TEXT", "origin TEXT", "direction TEXT",
        "pct REAL", "window_s INTEGER", "price REAL", "cohort TEXT",
        *(f"{c} REAL" for c in HORIZON_COLS),
        *(f"{c} REAL" for c in EXTREMA_COLS),
    ),
    "dex_alerts": (
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts REAL", "network TEXT", "token_name TEXT", "contract TEXT",
        "pool_ref TEXT", "direction TEXT", "pct REAL", "window_s INTEGER",
        "price_usd REAL", "liquidity_usd REAL",
        *(f"{c} REAL" for c in HORIZON_COLS),
        *(f"{c} REAL" for c in EXTREMA_COLS),
    ),
    "news_alerts": (
        "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "ts REAL", "source TEXT", "headline TEXT", "url TEXT",
    ),
}


def _grace(offset_s: int) -> float:
    return max(180.0, 0.05 * offset_s)


class Journal:
    def __init__(self, db_path: str, cohort: str = "A"):
        self.db_path = db_path
        self._cohort = cohort
        self._csv_dir = os.path.dirname(os.path.abspath(db_path))
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._migrate()
        self._csv_dirty = True
        self._last_csv_write = 0.0

    # -- schema -------------------------------------------------------------

    def _migrate(self) -> None:
        """Create tables; add missing columns without losing rows."""
        for table, columns in _SCHEMA.items():
            col_defs = ", ".join(columns)
            self._conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})")
            existing = {
                row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for col_def in columns:
                name = col_def.split()[0]
                if name not in existing:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        self._conn.execute("PRAGMA user_version = 1")
        self._conn.commit()

    # -- recording ------------------------------------------------------------

    def record_alert(self, alert, cohort: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO alerts (ts, asset, origin, direction, pct, window_s,"
            " price, cohort, min60, max60, min1440, max1440)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                alert.ts, alert.base, alert.origin, alert.direction,
                alert.pct, alert.window_s, alert.price_after,
                cohort or self._cohort,
                alert.price_after, alert.price_after,
                alert.price_after, alert.price_after,
            ),
        )
        self._conn.commit()
        self._csv_dirty = True
        log.info("journal: recorded CEX alert #%d %s %s", cur.lastrowid, alert.base, alert.direction)
        return cur.lastrowid

    def record_dex_alert(self, alert) -> int:
        cur = self._conn.execute(
            "INSERT INTO dex_alerts (ts, network, token_name, contract, pool_ref,"
            " direction, pct, window_s, price_usd, liquidity_usd,"
            " min60, max60, min1440, max1440)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                alert.ts, alert.chain, alert.token_name, alert.contract,
                alert.pool_address, alert.direction, alert.pct, alert.window_s,
                alert.price_usd, alert.liquidity_usd,
                alert.price_usd, alert.price_usd,
                alert.price_usd, alert.price_usd,
            ),
        )
        self._conn.commit()
        self._csv_dirty = True
        log.info("journal: recorded DEX alert #%d %s", cur.lastrowid, alert.token_symbol)
        return cur.lastrowid

    def record_news_alert(self, alert) -> int:
        cur = self._conn.execute(
            "INSERT INTO news_alerts (ts, source, headline, url) VALUES (?, ?, ?, ?)",
            (alert.ts, alert.source, alert.title, alert.url),
        )
        self._conn.commit()
        self._csv_dirty = True
        return cur.lastrowid

    def count(self, table: str) -> int:
        return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # -- price feeds ------------------------------------------------------------

    def on_cex_quotes(self, quotes, now: float) -> None:
        """Feed from the 5 s poll loop: {(venue, base): price}."""
        prices = {(q.venue, q.base): q.price for q in quotes}
        rows = self._conn.execute(
            "SELECT * FROM alerts WHERE ts >= ?", (now - OPEN_S,)
        ).fetchall()
        changed = False
        for row in rows:
            price = prices.get((row["origin"], row["asset"]))
            if price is None:
                continue
            updates = self._compute_updates(row, price, now)
            if updates:
                self._apply_updates("alerts", row["id"], updates)
                changed = True
        if changed:
            self._conn.commit()
            self._csv_dirty = True
        self._maybe_write_csvs(now)

    def on_dex_pairs(self, pairs, now: float) -> None:
        """Feed from the 60 s DexScreener poll.

        Pool selection: the alert's exact pool if present, else the most
        liquid pool of the same token on the same network.
        """
        by_pool = {p.pool_key: p for p in pairs}
        by_token: dict[tuple[str, str], list] = {}
        for p in pairs:
            by_token.setdefault((p.chain, p.token_address), []).append(p)
        for lst in by_token.values():
            lst.sort(key=lambda p: p.liquidity_usd, reverse=True)

        rows = self._conn.execute(
            "SELECT * FROM dex_alerts WHERE ts >= ?", (now - OPEN_S,)
        ).fetchall()
        changed = False
        for row in rows:
            pair = by_pool.get(f"{row['network']}:{row['pool_ref']}")
            if pair is None:
                candidates = by_token.get((row["network"], row["contract"]))
                pair = candidates[0] if candidates else None
            if pair is None:
                continue
            updates = self._compute_updates(row, pair.price_usd, now)
            if updates:
                self._apply_updates("dex_alerts", row["id"], updates)
                changed = True
        if changed:
            self._conn.commit()
            self._csv_dirty = True
        self._maybe_write_csvs(now)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _compute_updates(row: sqlite3.Row, price: float, now: float) -> dict:
        updates: dict[str, float] = {}
        age = now - row["ts"]
        for min_col, max_col, window in EXTREMA:
            if age <= window:
                cur_min, cur_max = row[min_col], row[max_col]
                if cur_min is None or price < cur_min:
                    updates[min_col] = price
                if cur_max is None or price > cur_max:
                    updates[max_col] = price
        for col, offset in HORIZONS:
            if row[col] is not None:
                continue  # already filled
            deadline = row["ts"] + offset
            # missed deadlines stay NULL forever: never backfill
            if deadline <= now <= deadline + _grace(offset):
                updates[col] = price
        return updates

    def _apply_updates(self, table: str, row_id: int, updates: dict) -> None:
        clause = ", ".join(f"{col} = ?" for col in updates)
        self._conn.execute(
            f"UPDATE {table} SET {clause} WHERE id = ?",
            (*updates.values(), row_id),
        )

    # -- CSV exports --------------------------------------------------------------

    def _maybe_write_csvs(self, now: float) -> None:
        if not self._csv_dirty:
            return
        if now - self._last_csv_write < CSV_WRITE_MIN_S:
            return
        self.write_csvs()
        self._last_csv_write = now
        self._csv_dirty = False

    def write_csvs(self) -> None:
        self._write_csv(
            "journal.csv",
            "SELECT * FROM alerts ORDER BY id",
        )
        outcome_cols = (
            "id, ts, asset, direction, pct, window_s, price, "
            + ", ".join(HORIZON_COLS)
            + ", "
            + ", ".join(EXTREMA_COLS)
        )
        self._write_csv("outcomes.csv", f"SELECT {outcome_cols} FROM alerts ORDER BY id")
        self._write_csv("dex_journal.csv", "SELECT * FROM dex_alerts ORDER BY id")
        self._write_csv("news_journal.csv", "SELECT * FROM news_alerts ORDER BY id")

    def _write_csv(self, filename: str, query: str) -> None:
        path = os.path.join(self._csv_dir, filename)
        cur = self._conn.execute(query)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([d[0] for d in cur.description])
            writer.writerows(cur.fetchall())
