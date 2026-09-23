import sqlite3
from config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
        timestamp INTEGER NOT NULL, price REAL NOT NULL,
        open_interest REAL NOT NULL, open_interest_usd REAL NOT NULL,
        funding_rate REAL)""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_symbol_timestamp ON snapshots(symbol,timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON snapshots(timestamp)")
    conn.execute("CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
    conn.commit(); conn.close()
