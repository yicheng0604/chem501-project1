# export_run_to_csv.py
# Minimal, plotting-free exporter:
# - Selects one experiment ("run") from SQLite
# - Optionally windows data between start/stop event timestamps
# - Writes three CSVs: measurements / events / metadata (conditions)
#
# Usage examples:
#   python export_run_to_csv.py --db project.db --out exports             # latest run
#   python export_run_to_csv.py --db project.db --exp-id exp-... --out .  # specific run
#   python export_run_to_csv.py --db project.db --full-run --out exports  # ignore start/stop window

import argparse, os, sqlite3, shutil
import pandas as pd
import numpy as np

def safe_connect(db_path: str):
    """
    Open the SQLite database in read-only mode.
    If the DB is busy/locked (e.g., logger running), create a snapshot copy and
    open that instead. Returns (connection, snapshot_path_or_None).
    """
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=1.0)
        con.execute("SELECT 1")
        return con, None
    except Exception:
        # Fallback: copy the DB (consistent snapshot) and read from it
        base, ext = os.path.splitext(db_path)
        snap = base + "_snapshot" + ext
        shutil.copy2(db_path, snap)
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True, timeout=1.0)
        con.execute("SELECT 1")
        print(f"[info] DB busy; using snapshot: {snap}")
        return con, snap

def pick_latest_exp(con):
    """
    Return the most recent exp_id by experiments.date_start.
    If no experiments table/rows, return None.
    """
    row = con.execute(
        "SELECT exp_id FROM experiments ORDER BY date_start DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None

def get_window(con, exp_id, start_label: str, end_label: str | None):
    """
    Resolve (t0, t1) time window for the given experiment:
    - t0 = first occurrence of `start_label` (e.g., 'start_cook')
    - t1 = first occurrence of `end_label` if provided,
           else try 'end_run' then 'stop_heat'
    Any missing endpoint returns as None.
    """
    def _first(label):
        row = con.execute(
            "SELECT ts_iso FROM events WHERE exp_id=? AND label=? ORDER BY ts_iso LIMIT 1",
            (exp_id, label)
        ).fetchone()
        return pd.to_datetime(row[0]) if row else None

    t0 = _first(start_label)
    t1 = None
    if end_label:
        t1 = _first(end_label)
    else:
        # Heuristic fallback for window end
        for lab in ("end_run", "stop_heat"):
            t1 = _first(lab)
            if t1 is not None:
                break
    return t0, t1

def export_run(db_path: str, out_dir: str, exp_id: str | None,
               start_label="start_cook", end_label=None, full_run=False):
    """
    Core export routine:
    - Select an exp_id (explicit or latest)
    - Read measurements/events/conditions for that exp_id
    - Optionally window measurements between t0..t1 (derived from events)
    - Add 'min_since_start' aligned to 'start_cook' (or first measurement)
    - Write three CSVs to `out_dir`
    """
    con, snap = safe_connect(db_path)
    try:
        # Choose experiment: explicit or latest
        if not exp_id:
            exp_id = pick_latest_exp(con)
        if not exp_id:
            raise SystemExit("[error] No experiments found.")

        # Read the three relevant tables for this exp_id
        meas = pd.read_sql_query(
            "SELECT ts_iso, temp_C, rh_pct, press_hPa FROM measurements "
            "WHERE exp_id=? ORDER BY ts_iso",
            con, params=[exp_id]
        )
        events = pd.read_sql_query(
            "SELECT ts_iso, label, value FROM events WHERE exp_id=? ORDER BY ts_iso",
            con, params=[exp_id]
        )
        conds = pd.read_sql_query(
            "SELECT key, value FROM conditions WHERE exp_id=?",
            con, params=[exp_id]
        )

        if meas.empty:
            raise SystemExit(f"[warn] No measurements for {exp_id}")

        # Make a DateTimeIndex for easy windowing and alignment
        meas["ts"] = pd.to_datetime(meas["ts_iso"])
        meas = meas.set_index("ts")

        # Optional windowing by event timestamps
        if not full_run:
            t0, t1 = get_window(con, exp_id, start_label, end_label)
            if t0 is not None:
                meas = meas[meas.index >= t0]
            if t1 is not None:
                meas = meas[meas.index <= t1]

        # Add alignment column: minutes since start
        # Use 'start_cook' if present; fallback = first measurement time
        if not events.empty:
            t0_ev = events.loc[events["label"] == start_label, "ts_iso"].min()
            t0_align = pd.to_datetime(t0_ev) if pd.notna(t0_ev) else None
        else:
            t0_align = None
        if t0_align is None and not meas.empty:
            t0_align = meas.index.min()

        meas["min_since_start"] = (
            (meas.index - t0_align).total_seconds()/60.0
            if t0_align is not None else np.nan
        )

        # Ensure output directory
        os.makedirs(out_dir, exist_ok=True)

        # Compose output paths
        m_out = os.path.join(out_dir, f"{exp_id}_measurements.csv")
        e_out = os.path.join(out_dir, f"{exp_id}_events.csv")
        c_out = os.path.join(out_dir, f"{exp_id}_metadata.csv")

        # Write measurements CSV with a clean ISO timestamp column + aligned minutes
        cols = ["ts_iso", "temp_C", "rh_pct", "press_hPa", "min_since_start"]
        meas_out = meas.reset_index()
        # Keep timestamps readable; if tz-aware, convert to UTC with offset
        meas_out["ts_iso"] = (
            meas_out["ts"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            if meas_out["ts"].dt.tz is not None
            else meas_out["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S")
        )
        meas_out = meas_out[cols]
        meas_out.to_csv(m_out, index=False)

        # Write events and metadata (conditions) as-is
        events.to_csv(e_out, index=False)
        conds.to_csv(c_out, index=False)

        print(f"[ok] Exported:\n  {m_out}\n  {e_out}\n  {c_out}")

    finally:
        # Always close; delete snapshot if we created one
        con.close()
        if snap and os.path.exists(snap):
            try:
                os.remove(snap)
            except Exception:
                pass

def main():
    parser = argparse.ArgumentParser(description="Filter & export run data (CSV).")
    parser.add_argument("--db", default="project.db", help="SQLite path")
    parser.add_argument("--out", default="exports", help="Output folder")
    parser.add_argument("--exp-id", help="Explicit experiment ID (else latest)")
    parser.add_argument("--full-run", action="store_true", help="Ignore start/stop window")
    parser.add_argument("--start-label", default="start_cook", help="Window start label")
    parser.add_argument("--end-label", default=None, help="Window end label (fallback to end_run/stop_heat)")
    args = parser.parse_args()

    export_run(
        db_path=args.db,
        out_dir=args.out,
        exp_id=args.exp_id,
        start_label=args.start_label,
        end_label=args.end_label,
        full_run=args.full_run
    )

if __name__ == "__main__":
    main()
