# mark_event_gui.py
# GUI event marker for your CHEM501 project.db
# Usage (example):
#   python mark_event_gui.py --db project.db
#
# What this does:
# - Opens a small Tkinter window to add time-stamped event rows into the `events` table.
# - Lets you pick an exp_id, choose an event label (e.g., start_cook, stop_heat, end_run),
#   optionally add notes (value), and store the timestamp as either "now" or a typed ISO string.
# - Also provides a simple viewer to list saved events for the selected exp_id.

import argparse, sqlite3, sys
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox

# Default labels that appear in the label Combobox and quick buttons.
# You can type a custom label directly into the label field if needed.
DEFAULT_LABELS = [
    "start_cook", "stop_heat", "end_run",   # ← includes end_run
    "lid_open", "lid_close",
    "stir",
    "window_open", "window_close",
    "hood_on", "hood_off",
    "add_ingredient",
]

# Minimal schema for the events table; created if missing.
SCHEMA_EVENTS = """
CREATE TABLE IF NOT EXISTS events(
  exp_id TEXT, ts_iso TEXT, label TEXT, value TEXT
);
"""

def iso_now_utc():
    """Return the current time in UTC as an ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()

def parse_ts(ts_arg: str, store_local: bool) -> str:
    """
    Parse a timestamp string into ISO8601.
    - If ts_arg is 'now' or empty: use the current time.
      * store_local=True  -> store local time with local offset
      * store_local=False -> store UTC
    - If ts_arg is an ISO-like string (e.g., '2025-12-07T22:35:00'):
      * If it lacks timezone info, attach local or UTC depending on store_local.
      * If it includes timezone info, convert to local or UTC accordingly.
    """
    if not ts_arg or ts_arg.strip().lower() == "now":
        return datetime.now().astimezone().isoformat() if store_local else iso_now_utc()
    try:
        dt = datetime.fromisoformat(ts_arg.strip())
    except Exception:
        raise ValueError("Time format error. Use ISO8601 like 2025-12-07T22:35:00 or 'now'.")
    # Attach or convert timezone depending on user choice.
    if dt.tzinfo is None:
        dt = dt.astimezone() if store_local else dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone() if store_local else dt.astimezone(timezone.utc)
    return dt.isoformat()

class EventGUI(tk.Tk):
    """
    A small Tkinter application for inserting and viewing events.
    Expected DB tables:
      - experiments(exp_id, date_start, location, device_id, notes) [not modified here]
      - events(exp_id, ts_iso, label, value)
    Only `events` is written by this GUI.
    """
    def __init__(self, db_path: str):
        super().__init__()
        self.title("Event Marker — project.db")
        self.geometry("560x360")
        self.resizable(False, False)
        self.db_path = db_path

        # Open SQLite connection and ensure the `events` table exists.
        self.conn = sqlite3.connect(self.db_path)
        with self.conn:
            self.conn.executescript(SCHEMA_EVENTS)

        # ----- UI layout -----
        pad = {'padx': 8, 'pady': 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        # exp_id selector (Combobox) + a Refresh button to reload exp_ids from `experiments`.
        ttk.Label(frm, text="Experiment ID (exp_id):").grid(row=0, column=0, sticky="w")
        self.exp_ids = self.load_exp_ids()
        self.exp_combo = ttk.Combobox(frm, values=self.exp_ids, state="normal")
        self.exp_combo.grid(row=0, column=1, sticky="ew")
        if self.exp_ids:
            self.exp_combo.set(self.exp_ids[0])
        else:
            # If there are no experiments yet, suggest a reasonable exp_id pattern.
            self.exp_combo.set(datetime.now().strftime("exp-%Y%m%d-%H%M%S"))

        self.btn_refresh_exp = ttk.Button(frm, text="Refresh", command=self.refresh_exp_ids)
        self.btn_refresh_exp.grid(row=0, column=2, sticky="w", **pad)

        # Event label: pick from DEFAULT_LABELS or type a custom label.
        ttk.Label(frm, text="Event label:").grid(row=1, column=0, sticky="w")
        self.label_combo = ttk.Combobox(frm, values=DEFAULT_LABELS, state="normal")
        self.label_combo.grid(row=1, column=1, sticky="ew")
        self.label_combo.set(DEFAULT_LABELS[0])

        # Quick-access buttons to set common labels into the label field (no DB writes yet).
        quick = ttk.Frame(frm)
        quick.grid(row=1, column=2, sticky="w")
        for i, (txt, val) in enumerate([
            ("Start", "start_cook"),
            ("Stop",  "stop_heat"),
            ("End",   "end_run"),
            ("Lid↑",  "lid_open"),
            ("Lid↓",  "lid_close"),
            ("Stir",  "stir")
        ]):
            ttk.Button(quick, text=txt, width=6,
                       command=lambda v=val: self.label_combo.set(v)).grid(row=0, column=i, padx=2)

        # Optional free-text notes field stored in `events.value` (e.g., base=spicy, water=1L).
        ttk.Label(frm, text="Notes (value):").grid(row=2, column=0, sticky="w")
        self.value_entry = ttk.Entry(frm)
        self.value_entry.grid(row=2, column=1, columnspan=2, sticky="ew")
        self.value_entry.insert(0, "base=spicy, water=1L, heat=max, window=closed, hood=off")

        # Timestamp controls:
        # - "Use now" toggles whether to store the current time or allow a custom ISO string.
        # - "Store local time (default UTC)" switches between local or UTC storage.
        self.use_now = tk.BooleanVar(value=True)
        self.local_tz = tk.BooleanVar(value=False)

        row3 = ttk.Frame(frm)
        row3.grid(row=3, column=0, columnspan=3, sticky="ew")
        ttk.Checkbutton(row3, text="Use now", variable=self.use_now, command=self.toggle_ts_entry).pack(side="left")
        ttk.Checkbutton(row3, text="Store local time (default UTC)", variable=self.local_tz).pack(side="left", padx=10)

        ttk.Label(frm, text="Timestamp (ISO or 'now'):").grid(row=4, column=0, sticky="w")
        self.ts_entry = ttk.Entry(frm)
        self.ts_entry.grid(row=4, column=1, columnspan=2, sticky="ew")
        self.ts_entry.insert(0, "now")
        self.ts_entry.configure(state="disabled")  # disabled when "Use now" is checked

        # Action buttons:
        # - Add Event: inserts one row into `events`.
        # - Show Events: opens a child window listing events for the chosen exp_id.
        # - Close: closes DB and exits the GUI.
        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Button(btns, text="Add Event", command=self.add_event).pack(side="left")
        ttk.Button(btns, text="Show Events", command=self.show_events).pack(side="left", padx=8)
        ttk.Button(btns, text="Close", command=self.on_close).pack(side="right")

        # Allow the exp_id Combobox column to expand with the window.
        frm.columnconfigure(1, weight=1)

    # -------- DB helpers --------
    def load_exp_ids(self):
        """Return a list of exp_id values from `experiments`, newest first."""
        try:
            rows = self.conn.execute(
                "SELECT exp_id FROM experiments ORDER BY date_start DESC"
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    def refresh_exp_ids(self):
        """Reload exp_ids from DB and update the Combobox."""
        self.exp_ids = self.load_exp_ids()
        self.exp_combo.configure(values=self.exp_ids)
        if self.exp_ids and self.exp_combo.get() not in self.exp_ids:
            self.exp_combo.set(self.exp_ids[0])
        messagebox.showinfo("Refreshed", f"Loaded {len(self.exp_ids)} experiment IDs.")

    # -------- UI helpers --------
    def toggle_ts_entry(self):
        """Enable or disable the timestamp entry field based on the 'Use now' checkbox."""
        if self.use_now.get():
            self.ts_entry.configure(state="disabled")
            self.ts_entry.delete(0, tk.END)
            self.ts_entry.insert(0, "now")
        else:
            self.ts_entry.configure(state="normal")
            if not self.ts_entry.get().strip() or self.ts_entry.get().strip().lower() == "now":
                # Provide a template ISO string for convenience.
                self.ts_entry.delete(0, tk.END)
                self.ts_entry.insert(0, datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

    # -------- Actions --------
    def add_event(self):
        """Insert one event row into `events` for the selected exp_id."""
        exp_id = self.exp_combo.get().strip()
        label  = self.label_combo.get().strip()
        value  = self.value_entry.get().strip()
        ts_arg = "now" if self.use_now.get() else self.ts_entry.get().strip()

        # Basic validation
        if not exp_id:
            messagebox.showerror("Error", "Please provide exp_id.")
            return
        if not label:
            messagebox.showerror("Error", "Please choose or enter a label.")
            return

        # Parse timestamp into ISO string (UTC or local depending on checkbox).
        try:
            ts_iso = parse_ts(ts_arg, store_local=self.local_tz.get())
        except Exception as e:
            messagebox.showerror("Time error", str(e))
            return

        # Write the event to DB.
        try:
            with self.conn:
                self.conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                                  (exp_id, ts_iso, label, value))
            messagebox.showinfo("OK", f"Event saved.\nexp_id={exp_id}\nts={ts_iso}\nlabel={label}")
        except Exception as e:
            messagebox.showerror("DB error", str(e))

    def show_events(self):
        """Open a small window listing all events for the selected exp_id."""
        exp_id = self.exp_combo.get().strip()
        if not exp_id:
            messagebox.showerror("Error", "Please provide exp_id.")
            return
        rows = self.conn.execute(
            "SELECT ts_iso,label,value FROM events WHERE exp_id=? ORDER BY ts_iso", (exp_id,)
        ).fetchall()

        win = tk.Toplevel(self)
        win.title(f"Events — {exp_id}")
        win.geometry("640x360")

        cols = ("ts_iso", "label", "value")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=200 if c!="value" else 360, anchor="w")
        tree.pack(fill="both", expand=True)

        for r in rows:
            tree.insert("", "end", values=r)

    def on_close(self):
        """Close the SQLite connection and destroy the window."""
        try:
            self.conn.close()
        except Exception:
            pass
        self.destroy()

def main():
    # CLI: allow switching DB path without editing the code.
    ap = argparse.ArgumentParser(description="GUI Event Marker for project.db")
    ap.add_argument("--db", default="project.db", help="SQLite DB path (default: project.db)")
    args = ap.parse_args()

    try:
        app = EventGUI(args.db)
        app.mainloop()
    except Exception as e:
        # If the GUI fails to launch, show a message box and exit with nonzero code.
        messagebox.showerror("Fatal", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
