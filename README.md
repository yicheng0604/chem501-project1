Kitchen Microclimate During Hotpot: A Sensor-Based Comparison Across Broth Types(CHEM 501)

##[1]End-to-end acquisition and analysis of temperature / relative humidity / pressure:
MKR WiFi 1010 + Nicla Sense ME (ESLOV) → MQTT (JSON, 1 Hz) → Python logger → SQLite → reproducible plots (Matplotlib/NumPy).
Two DB-backed GUIs (Events & Metadata) reduce manual errors and support FAIR.

##[2]Project layout:
HotpotEnvPublisher_MKR1010_Nicla_FAIR.ino   # Firmware: publishes temp_C/rh_pct/press_hPa @1 Hz
mqtt_logger.py                               # Subscribe MQTT → write SQLite (creates exp-YYYYmmdd-HHMMSS)
mqtt_listener.py                             # Debug-only: print MQTT messages
mark_event_gui.py                            # Event GUI: Start/Stop/End + custom tags (timestamped)
metadata_gui.py                              # Metadata GUI: flavour/ventilation/cookware/volume/persons/notes
fetch_weather.py                              # Postcode → lat/lon/city; store weather snapshot (conditions)
db_check.py                                  # Quick DB sanity check
plot_scatter_fit_temperature.py              # Temperature: scatter + smooth + linear fit (first 20 min)
plot_scatter_fit_humidity.py                 # Humidity: scatter + smooth + linear fit
plot_scatter_fit_pressure.py                 # Pressure: scatter + smooth + linear fit

##[3]Quick start:
###1) Install dependencies
pip install paho-mqtt numpy pandas matplotlib requests

###2) Run a new experiment (typical order)
####1.Start the logger
python mqtt_logger.py --db project.db

Subscribes to devices/<device_id>/(status|heartbeat|data).
Creates a new exp-YYYYmmdd-HHMMSS entry in experiments.

####2.Mark events (Start / Stop / End)
python mark_event_gui.py --db project.db

Click Start before heating; Stop/End when finished.
Optional custom labels (e.g., stir, lid_open, etc.).

####3.Record metadata
python metadata_gui.py

Select the correct exp_id.
Fill broth_flavour, ventilation, pot_size, broth_volume, persons, notes, city, etc.

####4.Add weather (optional but recommended)
python fetch_weather.py --db project.db --exp-id <exp_id> --postcode "L6 1AJ"

####5.Quick database check
python db_check.py


####6.Make figures 
python plot_scatter_fit_temperature.py --db project.db --out figures --x-max 40
python plot_scatter_fit_humidity.py    --db project.db --out figures --x-max 40
python plot_scatter_fit_pressure.py    --db project.db --out figures --x-max 40

If the DB is locked by the logger, the plotting scripts automatically read a read-only snapshot.

##[4]MQTT & payloads:
Broker: test.mosquitto.org

Topics:
devices/<device_id>/status (online, ready, etc.)
devices/<device_id>/heartbeat ({"ms": <uptime_ms>})
devices/<device_id>/data

##[5]Database schema (SQLite):
1.experiments(exp_id TEXT PRIMARY KEY, date_start TEXT, location TEXT, device_id TEXT, notes TEXT)

2.measurements(exp_id TEXT, ts_iso TEXT, iaq REAL, iaq_accuracy INT, b_voc_eq_ppm REAL, eco2_ppm REAL, temp_C REAL, rh_pct REAL, press_hPa REAL, quality_flag TEXT)

3.events(exp_id TEXT, ts_iso TEXT, label TEXT, value TEXT)
(common labels: start_cook, stop_heat, end_run, stir, hood_on/off, lid_open/close, …)

4.conditions(exp_id TEXT, key TEXT, value TEXT, PRIMARY KEY(exp_id,key))
(examples: broth_flavour, ventilation, pot_size, broth_volume, persons, notes, lat, lon, city, weather_*)

##[6]Reproducible plots:
1.Each plot shows raw scatter, smoothed trend, and a solid linear fit over the first 20 min.

2.Right of the legend: stacked Slope & R² list per flavour.

3.Unified style: consistent palette, line widths, marker sizes, fonts.

##[7]FAIR checklist:
1.Findable / Accessible: single portable project.db (+ figures written to figures/).

2.Interoperable: MQTT JSON; ISO-8601 timestamps; clear relational schema.

3.Reusable: Events/metadata/weather are tied to exp_id; scripts are parameterised; version-agnostic.

##[8]Troubleshooting:
1.Zeros or nulls at start: the firmware implements gated start & self-healing; allow a short warm-up and check ESLOV cabling.

2.Empty plots: ensure start_cook was marked (scripts will fall back to first measurement, but proper labeling is best).

3.DB is busy/locked: plotting scripts automatically switch to a snapshot; or briefly stop mqtt_logger.py during analysis.
