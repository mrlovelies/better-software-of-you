import sqlite3
conn = sqlite3.connect("/home/mrlovelies/.local/share/software-of-you/soy.db")
cur = conn.cursor()

cur.execute("""
SELECT rs.name, rf.title, rf.content, rf.source_url, rf.relevance_score, rf.finding_type, rf.created_at, rf.cross_stream_ids
FROM research_findings rf
JOIN research_streams rs ON rf.stream_id = rs.id
WHERE rf.created_at >= datetime('now','-7 days')
ORDER BY rs.priority DESC, rf.relevance_score DESC
""")
rows = cur.fetchall()
for r in rows:
    print("---FINDING---")
    print("Stream:", r[0])
    print("Title:", r[1])
    content = r[2] if r[2] else "N/A"
    print("Content:", content[:600])
    print("URL:", r[3])
    print("Relevance:", r[4])
    print("Type:", r[5])
    print("Date:", r[6])
    print("Cross-stream:", r[7])
    print()

print("===STREAMS===")
cur.execute("SELECT id, name, priority, description FROM research_streams WHERE enabled=1 ORDER BY priority DESC")
for s in cur.fetchall():
    desc = s[3][:120] if s[3] else ""
    print("ID:%d | %s (pri:%d) | %s" % (s[0], s[1], s[2], desc))

conn.close()
