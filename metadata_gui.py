# metadata_gui.py — Simple GUI to tag metadata to experiments (SQLite)
# Purpose:
#   - Provide a small Tkinter window to attach key–value metadata
#     (e.g., broth_flavour, ventilation, location) to an existing experiment.
#   - Writes into a table named `conditions` with a composite primary key (exp_id, key).
# Usage:
#   - Run this file (python metadata_gui.py). It will look for `project.db`
#     in the current folder, list recent `experiments.exp_id`, and let you
#     save metadata under the selected exp_id.

import sqlite3, tkinter as tk
from tkinter import ttk, messagebox

# SQLite database file (same folder by default)
DB = "project.db"

def get_experiments(con):
    """
    Return a list of existing experiment IDs (exp_id) from the `experiments` table,
    newest first by date_start. This function is read-only.
    """
    return [r[0] for r in con.execute(
        "SELECT exp_id FROM experiments ORDER BY date_start DESC").fetchall()]

def ensure_tables(con):
    """
    Ensure the `conditions` table exists.
    Schema:
      conditions(
        exp_id TEXT,
        key    TEXT,
        value  TEXT,
        PRIMARY KEY (exp_id, key)
      )
    This lets you maintain one unique value per (exp_id, key).
    """
    con.execute("""CREATE TABLE IF NOT EXISTS conditions(
        exp_id TEXT, key TEXT, value TEXT,
        PRIMARY KEY (exp_id, key)
    )""")
    con.commit()

def save_metadata():
    """
    Collect all field values from the GUI and write them as key–value pairs
    into the `conditions` table for the selected exp_id. Only non-empty
    entries are written (so blank inputs do not overwrite existing values).
    """
    exp_id = exp_var.get().strip()
    if not exp_id:
        messagebox.showerror("Error", "No experiment selected"); return

    # Gather all inputs into a dict; keys become `conditions.key`,
    # values become `conditions.value`.
    items = {
        "broth_flavour": flavour_var.get().strip(),
        "ventilation":   vent_var.get().strip(),
        "window":        window_var.get().strip(),
        "hood":          hood_var.get().strip(),
        "location":      loc_var.get().strip(),
        "city":          city_var.get().strip(), 
        "pot_size":      pot_var.get().strip(),
        "broth_volume":  vol_var.get().strip(),
        "persons":       persons_var.get().strip(),
        "notes":         notes_var.get("1.0","end").strip()
    }

    # Write each non-empty item into the `conditions` table
    with sqlite3.connect(DB) as con:
        ensure_tables(con)
        for k, v in items.items():
            if v:  # only save non-empty values
                con.execute(
                    "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
                    (exp_id, k, v)
                )
        con.commit()

    messagebox.showinfo("Saved", f"Metadata saved for {exp_id}")

# ----- Build GUI -----
root = tk.Tk()
root.title("Experiment Metadata")

# Load recent experiment IDs for the dropdown (Combobox)
with sqlite3.connect(DB) as con:
    exps = get_experiments(con)
default_exp = exps[0] if exps else ""

# Tkinter StringVars backing the input widgets (with sensible defaults)
exp_var = tk.StringVar(value=default_exp)
flavour_var = tk.StringVar(value="spicy")       # e.g. spicy, tomato, tom yum, mushroom
vent_var    = tk.StringVar(value="hood_on")     # free text, e.g. hood_on/off, window_open/closed
window_var  = tk.StringVar(value="closed")
hood_var    = tk.StringVar(value="off")
loc_var     = tk.StringVar(value="kitchen")     # location tag
city_var    = tk.StringVar(value="Liverpool, GB")  # city tag (used by weather scripts)
pot_var     = tk.StringVar(value="24cm")
vol_var     = tk.StringVar(value="1.5L")
persons_var = tk.StringVar(value="2")

# Main frame and layout configuration
frm = ttk.Frame(root, padding=12); frm.grid(sticky="nsew")
root.columnconfigure(0, weight=1); root.rowconfigure(0, weight=1)

def add_row(r, label, widget):
    """
    Convenience function to place a two-column row:
      - left: field label
      - right: entry widget bound to a StringVar
    """
    ttk.Label(frm, text=label, width=16).grid(row=r, column=0, sticky="e", pady=3)
    widget.grid(row=r, column=1, sticky="ew", pady=3)
    frm.columnconfigure(1, weight=1)

# Experiment selector
ttk.Label(frm, text="Experiment:").grid(row=0, column=0, sticky="e", pady=3)
exp_cb = ttk.Combobox(frm, textvariable=exp_var, values=exps, state="readonly", width=40)
exp_cb.grid(row=0, column=1, sticky="ew", pady=3)

# Key–value fields (right column are Entry widgets backed by StringVars)
add_row(1, "broth_flavour", ttk.Entry(frm, textvariable=flavour_var))
add_row(2, "ventilation",   ttk.Entry(frm, textvariable=vent_var))
add_row(3, "window",        ttk.Entry(frm, textvariable=window_var))
add_row(4, "hood",          ttk.Entry(frm, textvariable=hood_var))
add_row(5, "location",      ttk.Entry(frm, textvariable=loc_var))
add_row(6, "city",          ttk.Entry(frm, textvariable=city_var))
add_row(7, "pot_size",      ttk.Entry(frm, textvariable=pot_var))
add_row(8, "broth_volume",  ttk.Entry(frm, textvariable=vol_var))
add_row(9, "persons",       ttk.Entry(frm, textvariable=persons_var))

# Multi-line notes (stored under key = "notes")
ttk.Label(frm, text="notes").grid(row=10, column=0, sticky="ne", pady=3)
notes_var = tk.Text(frm, height=5); notes_var.grid(row=10, column=1, sticky="nsew", pady=3)
frm.rowconfigure(10, weight=1)

# Save button → writes all non-empty fields to `conditions`
btn = ttk.Button(frm, text="Save Metadata", command=save_metadata)
btn.grid(row=11, column=0, columnspan=2, pady=8)

# Reasonable minimum window size
root.minsize(520, 420)

# Start Tkinter event loop
root.mainloop()
