# plot_scatter_fit_temperature.py
# Temperature (°C): scatter + smoothed + solid linear fit (by flavor).
# Slope/R^2 labels are shown as a stacked list to the RIGHT of the legend.

import argparse, os, re, json, sqlite3, shutil
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ---- unified style ----
# Consistent visual settings across figures (FAIR: reproducible appearance)
PALETTE   = ["#0072B2", "#E69F00", "#009E73", "#D55E00"]  # blue, orange, green, red
FS_LABEL  = 20   # axis label font size
FS_TICK   = 14   # tick label font size
FS_LEG    = 14   # legend font size
FS_SLOPE  = 12   # slope/R^2 text font size
LW_TREND  = 2.6  # smoothed trendline width
LW_FIT    = 1.8  # linear-fit line width
MS_SCAT   = 14   # scatter marker size
FRAME_LW  = 1.4  # plot frame (axes spines) line width

# ---- DB helpers ----
def safe_connect(db_path: str):
    """
    Open SQLite DB read-only. If the DB is locked by another process (e.g., live logger),
    create a snapshot copy and read from it so plotting is never blocked.
    Returns (connection, path_used, used_snapshot_flag).
    """
    uri = f"file:{db_path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=1.0); con.execute("SELECT 1")
        return con, db_path, False
    except Exception:
        # Fallback: copy DB to a snapshot and open that in read-only mode
        base, ext = os.path.splitext(db_path); snap = base + "_snapshot" + ext
        shutil.copy2(db_path, snap)
        con = sqlite3.connect(f"file:{snap}?mode=ro", uri=True, timeout=1.0); con.execute("SELECT 1")
        print(f"[info] DB busy; using snapshot: {snap}")
        return con, snap, True

def latest_exp_ids(con, n:int):
    """
    Return the latest n experiment IDs by most recent measurement timestamp.
    This lets you plot the most recent runs without specifying IDs manually.
    """
    rows = con.execute("""SELECT m.exp_id, MAX(m.ts_iso) tmax
                          FROM measurements m GROUP BY m.exp_id
                          ORDER BY tmax DESC LIMIT ?""",(n,)).fetchall()
    return [r[0] for r in rows]

def start_time(con, exp_id):
    """
    Alignment reference: use first 'start_cook' event if present; otherwise,
    fall back to the first measurement timestamp.
    """
    r = con.execute("SELECT ts_iso FROM events WHERE exp_id=? AND label='start_cook' ORDER BY ts_iso LIMIT 1",
                    (exp_id,)).fetchone()
    if r: return pd.to_datetime(r[0], errors="coerce")
    r2 = con.execute("SELECT ts_iso FROM measurements WHERE exp_id=? ORDER BY ts_iso LIMIT 1",
                     (exp_id,)).fetchone()
    return pd.to_datetime(r2[0], errors="coerce") if r2 else None

def series_temp(con, exp_id)->pd.Series:
    """
    Load temperature series (°C) as a pandas Series indexed by timestamp.
    Drop NaNs and duplicate timestamps; return a clean, sorted series.
    """
    df = pd.read_sql_query("SELECT ts_iso, temp_C FROM measurements WHERE exp_id=? ORDER BY ts_iso",
                           con, params=(exp_id,))
    if df.empty: return pd.Series(dtype=float)
    s = pd.Series(df["temp_C"].values, index=pd.to_datetime(df["ts_iso"]), name="temp_C")
    s = s.dropna(); s = s[~s.index.duplicated(keep="first")]
    return s.sort_index()

# ---- flavor parsing ----
# Human-readable label for each experiment obtained from notes/metadata.
KEYS = ["base","flavor","flavour"]

def _from_kv_text(txt):
    """
    Parse key–value style text such as 'flavor=tomato' or 'base: spicy'.
    Returns the first matched value for keys in KEYS.
    """
    for k in KEYS:
        m = re.search(rf"{k}\s*[:=]\s*([^,;|\n]+)", txt, flags=re.IGNORECASE)
        if m: return m.group(1).strip()
    return None

def _from_json_text(txt):
    """
    If the notes/metadata is JSON, read keys defined in KEYS.
    """
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            for k in KEYS:
                if k in obj and str(obj[k]).strip(): return str(obj[k]).strip()
    except Exception: 
        pass
    return None

def flavor_label(con, exp_id):
    """
    Determine the 'flavor' legend label:
      1) experiments.notes (JSON or KV text),
      2) latest 'metadata' or 'start_cook' event value,
      3) fallback to exp_id if nothing else is available.
    """
    row = con.execute("SELECT notes FROM experiments WHERE exp_id=?", (exp_id,)).fetchone()
    notes = (row[0] or "") if row else ""
    lab = _from_json_text(notes) or _from_kv_text(notes)
    if not lab:
        ev = con.execute("""SELECT value FROM events 
                            WHERE exp_id=? AND label IN ('metadata','start_cook')
                            ORDER BY ts_iso DESC LIMIT 1""",(exp_id,)).fetchone()
        if ev and ev[0]: lab = _from_json_text(ev[0]) or _from_kv_text(ev[0])
    return (lab or exp_id).strip()

# ---- smoothing & fitting ----
def smooth_series(s:pd.Series, w:int)->pd.Series:
    """
    Resample to 1 s, interpolate short gaps, then apply a centered rolling mean
    (window = w seconds). Keeps the raw points separate for scatter plotting.
    """
    if s.empty or w<=0: return s
    s1 = s.resample("1s").mean().interpolate("time", limit=5)
    return s1.rolling(f"{max(1,w)}s", center=True, min_periods=1).mean()

def linfit_xy(x,y):
    """
    Robust linear fit of y = m*x + b with R^2.
    Uses numpy.polyfit, falls back to lstsq on failure (e.g., ill-conditioned).
    Returns (m, b, r2) as floats (NaN if insufficient data).
    """
    msk = np.isfinite(x) & np.isfinite(y); x2,y2 = x[msk], y[msk]
    if len(x2)<2: return np.nan, np.nan, np.nan
    try:
        m,b = np.polyfit(x2,y2,1)
    except Exception:
        A = np.vstack([x2, np.ones_like(x2)]).T
        sol,*_ = np.linalg.lstsq(A,y2,rcond=None); m,b = float(sol[0]), float(sol[1])
    y_pred = m*x2 + b
    ss_res = np.sum((y2-y_pred)**2); ss_tot = np.sum((y2-np.mean(y2))**2)
    r2 = 1 - ss_res/ss_tot if ss_tot>0 else np.nan
    return float(m), float(b), float(r2)

# ---- main ----
def main():
    ap = argparse.ArgumentParser(description="Temperature (°C): scatter + smoothed + solid linear fit (by flavor).")
    # Inputs and selection
    ap.add_argument("--db", default="project.db")
    ap.add_argument("--out", default=None)
    ap.add_argument("--exp-ids", nargs="+")
    ap.add_argument("--n", type=int, default=4)
    # Plot controls (sampling, smoothing, fit window, x-range)
    ap.add_argument("--downsample-sec", type=int, default=10)
    ap.add_argument("--smooth-sec", type=int, default=30)
    ap.add_argument("--fit-minutes", type=float, default=20.0)
    ap.add_argument("--x-max", type=float, default=40.0)
    # Where to place the stacked slope/R^2 labels (axes fraction coordinates)
    ap.add_argument("--side-label-x", type=float, default=0.18,
                    help="Axes fraction X for the stacked slope labels (to the right of legend).")
    ap.add_argument("--side-label-y0", type=float, default=0.92,
                    help="Starting Y (axes fraction) for stacked slope labels.")
    ap.add_argument("--side-label-dy", type=float, default=0.05,
                    help="Vertical spacing between stacked labels (axes fraction).")
    args = ap.parse_args()

    # Resolve output directory
    db_abs = os.path.abspath(args.db)
    outdir = os.path.abspath(args.out) if args.out else os.path.dirname(db_abs)
    os.makedirs(outdir, exist_ok=True)

    # Open DB (or snapshot) and select experiments (explicit IDs or latest N)
    con, used_path, used_snapshot = safe_connect(db_abs)
    exp_ids = args.exp_ids if args.exp_ids else latest_exp_ids(con, args.n)
    if not exp_ids: print("[warn] no experiments found"); return

    # Prepare figure and container for slope/R^2 texts
    plt.figure(figsize=(12,7)); ax = plt.gca()
    slope_rows = []  # will be displayed near the legend as a stacked list

    for i,exp in enumerate(exp_ids):
        # Load temperature series and alignment reference
        s = series_temp(con, exp)
        if s.empty: continue
        t0 = start_time(con, exp)
        if t0 is None or pd.isna(t0): continue

        # Align to start and crop to first x_max minutes
        s = s[(s.index >= t0) & (s.index <= t0 + pd.to_timedelta(args.x_max, unit="m"))]
        if s.empty: continue

        # Downsample for scatter + compute smoothed trend
        ds = s.resample(f"{max(1,args.downsample_sec)}s").mean()
        x  = (ds.index - t0).total_seconds()/60.0; y = ds.values.astype(float)
        ss = smooth_series(s, args.smooth_sec)
        xs = (ss.index - t0).total_seconds()/60.0; ys = ss.values.astype(float)

        # Linear fit over the initial fit-minutes window
        mask = x <= float(args.fit_minutes)
        m,b,r2 = linfit_xy(x.values[mask], y[mask])

        # Color and legend label for this experiment
        color = PALETTE[i % len(PALETTE)]
        flav  = flavor_label(con, exp)

        # Draw raw scatter points (no connecting raw line)
        ax.scatter(x, y, s=MS_SCAT, alpha=0.45, edgecolors="none", color=color)
        # Smoothed trend line
        ax.plot(xs, ys, lw=LW_TREND, color=color, label=flav)

        # Linear-fit line extended to the end of the series
        if np.isfinite(m):
            x_fit = np.linspace(0, float(x.max()), 200)
            y_fit = m*x_fit + b
            ax.plot(x_fit, y_fit, lw=LW_FIT, color=color, alpha=0.9)
            # Store formatted slope/R^2 for stacked display near legend
            slope_rows.append((flav, color, f"Slope = {m:.3f} °C/min, $R^2$ = {r2:.3f}"))

    con.close()

    # Axes labels and limits + a black frame for a “good graph” look
    ax.set_xlabel("Time since start (minutes)", fontsize=FS_LABEL)
    ax.set_ylabel("Temperature (°C)", fontsize=FS_LABEL)
    ax.tick_params(labelsize=FS_TICK)
    ax.set_xlim(0, args.x_max)
    for sp in ["top","right","left","bottom"]:
        ax.spines[sp].set_visible(True)
        ax.spines[sp].set_linewidth(FRAME_LW)
        ax.spines[sp].set_edgecolor("black")

    # Legend (upper left), then stacked slope/R^2 texts to its right (axes coords)
    ax.legend(title="Flavor", fontsize=FS_LEG, title_fontsize=FS_LEG, frameon=False, loc="upper left")
    x0, y0, dy = args.side_label_x, args.side_label_y0, args.side_label_dy
    for k, (flav, color, txt) in enumerate(slope_rows):
        ax.text(x0, y0 - k*dy, txt, transform=ax.transAxes, color=color,
                fontsize=FS_SLOPE, ha="left", va="center")

    # Save figure next to the DB (or in --out)
    plt.tight_layout()
    out_png = os.path.join(outdir, "overlay_scatter_fit_temperature.png")
    plt.savefig(out_png, dpi=200, bbox_inches="tight"); plt.close()
    print(f"Saved → {out_png}")

if __name__ == "__main__":
    main()
