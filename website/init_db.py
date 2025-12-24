import sqlite3
import os

DB_PATH = "/home/bbdwz/projects/website/tracker.db"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tracker_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_number TEXT NOT NULL,
    interval_minutes INTEGER DEFAULT 60,
    enabled INTEGER DEFAULT 1,
    last_checked TIMESTAMP,
    last_result TEXT
)
""")

conn.commit()
conn.close()
print("✅ 初始化完成:", DB_PATH)
