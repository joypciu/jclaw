import sqlite3
conn = sqlite3.connect('store/messages.db')
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables:", [t[0] for t in tables])
try:
    rows = conn.execute("SELECT name,folder,jid FROM registered_groups").fetchall()
    print("Groups:", rows)
except Exception as e:
    print("Error:", e)
