import sqlite3
conn = sqlite3.connect("/home/mrlovelies/.local/share/software-of-you/soy.db")
cur = conn.cursor()

cur.execute("PRAGMA table_info(research_streams)")
print("Stream columns:", cur.fetchall())

cur.execute("SELECT * FROM research_streams ORDER BY priority DESC")
for s in cur.fetchall():
    print("---STREAM---")
    for i, col in enumerate(cur.description):
        print(col[0], ":", s[i])
    print()

# Get full content of most recent finding per stream
cur.execute("""
SELECT rs.name, rf.title, rf.content, rf.created_at
FROM research_findings rf
JOIN research_streams rs ON rf.stream_id = rs.id
WHERE rf.created_at >= datetime('now','-3 days')
ORDER BY rs.priority DESC, rf.created_at DESC
""")
for r in cur.fetchall():
    print("===FULL FINDING===")
    print("Stream:", r[0])
    print("Title:", r[1])
    print("Date:", r[3])
    print("Content:", r[2][:1200] if r[2] else "N/A")
    print()

conn.close()
