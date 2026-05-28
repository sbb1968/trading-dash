import sqlite3
conn = sqlite3.connect('trading_dash.db')
cur = conn.cursor()

# Vis hvilke tabeller findes
print('Tabeller:')
cur.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
tables = [r[0] for r in cur.fetchall()]
for t in tables:
    print(f'  {t}')

print()

# For hver tabel - vis kolonner
for t in tables:
    print(f'Kolonner i {t}:')
    cur.execute(f'PRAGMA table_info({t})')
    for row in cur.fetchall():
        print(f'  {row[1]:25s} {row[2]}')
    print()

conn.close()
