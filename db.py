# db.py
import sqlite3
import os
from datetime import datetime
from typing import Any, Optional, List, Dict, Tuple

DB_PATH = os.getenv("DB_PATH", "bybit_bot.db")

def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db(default_strategy_params: Dict[str, Any] = None):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS strategy_params (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            description TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            ts TEXT,
            symbol TEXT,
            side TEXT,
            action TEXT, -- open/close/partial_close
            qty REAL,
            price REAL,
            usdt_value REAL,
            pnl_usdt REAL,
            info TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pairs (
            symbol TEXT PRIMARY KEY,
            timeframe TEXT
        )
    """)
    conn.commit()

    # insert default strategy params if given
    if default_strategy_params:
        for k, v in default_strategy_params.items():
            cur.execute("INSERT OR IGNORE INTO strategy_params(name, value) VALUES(?,?)", (k, str(v)))
        conn.commit()
    conn.close()

def get_param(name: str) -> Optional[str]:
    conn = _conn()
    row = conn.execute("SELECT value FROM strategy_params WHERE name=?", (name,)).fetchone()
    conn.close()
    if row:
        return row[0]
    return None

def set_param(name: str, value: Any):
    conn = _conn()
    conn.execute("REPLACE INTO strategy_params(name, value) VALUES(?,?)", (name, str(value)))
    conn.commit()
    conn.close()

def create_run(description: str) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO runs(ts, description) VALUES(?,?)", (datetime.utcnow().isoformat(), description))
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id

def log_trade(run_id: Optional[int], symbol: str, side: str, action: str, qty: float, price: float, usdt_value: float, pnl: Optional[float], info: str = ""):
    conn = _conn()
    conn.execute(
        "INSERT INTO trades(run_id,ts,symbol,side,action,qty,price,usdt_value,pnl_usdt,info) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (run_id, datetime.utcnow().isoformat(), symbol, side, action, qty, price, usdt_value, pnl, info)
    )
    conn.commit()
    conn.close()

# pairs helpers
def add_pair(symbol: str, timeframe: str):
    conn = _conn()
    conn.execute("REPLACE INTO pairs(symbol, timeframe) VALUES(?,?)", (symbol, timeframe))
    conn.commit()
    conn.close()

def remove_pair(symbol: str):
    conn = _conn()
    conn.execute("DELETE FROM pairs WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()

def list_pairs() -> List[Tuple[str,str]]:
    conn = _conn()
    rows = conn.execute("SELECT symbol, timeframe FROM pairs").fetchall()
    conn.close()
    return rows

def list_trades(limit: int = 1000):
    conn = _conn()
    rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows
