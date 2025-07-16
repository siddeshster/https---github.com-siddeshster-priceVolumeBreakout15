import sqlite3

with open("init_scm.sql", "r") as f:
    sql = f.read()

conn = sqlite3.connect("signals.db")
conn.executescript(sql)
conn.commit()
conn.close()

print("✅ Database initialized.")
