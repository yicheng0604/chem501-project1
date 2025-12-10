"""
Quick sanity check for the local SQLite database `project.db`.

FINDABLE  : expects the tables: experiments, measurements, events
ACCESSIBLE: reads a local SQLite file (no writes)
INTEROPERABLE:
  - prints table row counts
  - prints last 3 rows from `measurements` with (ts_iso, temp_C, rh_pct, press_hPa)
REUSABLE  : read-only usage pattern; safe to run repeatedly
"""

import sqlite3

# Open a connection to the database file in the current working directory.
# (Tip: for read-only access you can use: sqlite3.connect("file:project.db?mode=ro", uri=True))
con = sqlite3.connect("project.db")

# For each expected table, query COUNT(*) and print "<table> <row_count>".
for t in ["experiments", "measurements", "events"]:
    # execute(...) returns a cursor; fetchone()[0] extracts the scalar count.
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(t, n)

# Print the 3 most recent measurement records (by timestamp, descending).
# Output is a list of tuples: [(ts_iso, temp_C, rh_pct, press_hPa), ...]
print(
    "last 3 rows:",
    con.execute(
        "SELECT ts_iso,temp_C,rh_pct,press_hPa "
        "FROM measurements "
        "ORDER BY ts_iso DESC "
        "LIMIT 3"
    ).fetchall()
)

# Always close the connection to release the file handle.
con.close()
