import sqlite3

conn = sqlite3.connect('/home/mrlovelies/.software-of-you/data/soy.db')
cur = conn.cursor()

print("=== research_findings SCHEMA ===")
cur.execute("PRAGMA table_info(research_findings)")
print(cur.fetchall())

print("\n=== learning_digests SCHEMA ===")
cur.execute("PRAGMA table_info(learning_digests)")
print(cur.fetchall())

print("\n=== research_digests SCHEMA ===")
cur.execute("PRAGMA table_info(research_digests)")
print(cur.fetchall())
