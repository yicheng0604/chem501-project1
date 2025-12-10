import json, sqlite3
from datetime import datetime, timezone
from paho.mqtt import client as mqtt

# ---------- Configuration ----------
# Default device/topic identity and broker location.
# If you change DEVICE_ID in your firmware, update DEFAULT_DEVICE_ID here too.
DEFAULT_DEVICE_ID = "mkr-kitchen-01"
BROKER            = "test.mosquitto.org"   # public test broker (data is public)
PORT              = 1883                   # standard MQTT (unencrypted) port
DB_PATH           = "project.db"           # SQLite database file path
# -----------------------------------

# SQLite schema:
# - experiments: one row per run (exp_id primary key)
# - measurements: timeseries rows with sensor values
# - events: discrete events (status, heartbeat, user-labeled events, etc.)
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS experiments(
  exp_id TEXT PRIMARY KEY, date_start TEXT, location TEXT, device_id TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS measurements(
  exp_id TEXT, ts_iso TEXT,
  iaq REAL, iaq_accuracy INTEGER, b_voc_eq_ppm REAL, eco2_ppm REAL,
  temp_C REAL, rh_pct REAL, press_hPa REAL, quality_flag TEXT
);
CREATE TABLE IF NOT EXISTS events(
  exp_id TEXT, ts_iso TEXT, label TEXT, value TEXT
);
"""

def iso_now():
    """Return current timestamp as ISO-8601 in UTC (portable, sortable)."""
    return datetime.now(timezone.utc).isoformat()

def compute_quality(acc, buf):
    """
    Lightweight quality flag for IAQ stream:
      - If 'acc' (IAQ accuracy) is missing or <2 → 'acc<2'
      - Else if we have at least 30 points, mark 'unstable' when std. dev. > 5
      - Else 'ok'
    'buf' is a rolling buffer of IAQ floats used to assess variability.
    """
    if acc is None or (isinstance(acc, int) and acc < 2):
        return "acc<2"
    if len(buf) >= 30:
        m  = sum(buf)/len(buf)
        sd = (sum((x-m)**2 for x in buf)/len(buf))**0.5
        if sd > 5.0:
            return "unstable"
    return "ok"

def main():
    # Derive the experiment id (timestamp-based) and topic map.
    device_id = DEFAULT_DEVICE_ID
    exp_id = datetime.now().strftime("exp-%Y%m%d-%H%M%S")
    topics = {
        "status":    f"devices/{device_id}/status",
        "heartbeat": f"devices/{device_id}/heartbeat",
        "data":      f"devices/{device_id}/data",
    }

    # Open (or create) the SQLite DB and ensure schema exists.
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    # Create a new experiments row for this logger run if not already present.
    if not conn.execute("SELECT 1 FROM experiments WHERE exp_id=?", (exp_id,)).fetchone():
        conn.execute("INSERT INTO experiments VALUES (?,?,?,?,?)",
                     (exp_id, iso_now(), "kitchen", device_id, "init"))
        conn.commit()

    # Rolling buffer for IAQ values to evaluate variability.
    iaq_buf = []

    # ---- MQTT callbacks ----
    def on_connect(c, u, f, rc):
        """
        Called after the TCP+MQTT handshake completes.
        rc == 0 indicates success; nonzero indicates broker-side errors.
        """
        print("Connected rc=", rc)
        # Subscribe to all topics for this device. QoS=1 = at-least-once delivery.
        for t in topics.values():
            c.subscribe(t, qos=1)
        print("Subscribed:", topics)
        print(f"Logging to {DB_PATH}  |  exp_id={exp_id}  |  device_id={device_id}")

    def on_message(c, u, msg):
        """
        Called for every message that arrives on any subscribed topic.
        Routes messages to:
          - events table (status / heartbeat)
          - measurements table (data JSON payload)
        Also prints a compact console line for quick monitoring.
        """
        ts = iso_now()

        # ---- status topic → events table ----
        if msg.topic == topics["status"]:
            conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                         (exp_id, ts, "status", msg.payload.decode("utf-8","ignore")))
            conn.commit()
            print(msg.topic, msg.payload.decode("utf-8","ignore"))
            return

        # ---- heartbeat topic → events table ----
        if msg.topic == topics["heartbeat"]:
            conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                         (exp_id, ts, "heartbeat", msg.payload.decode("utf-8","ignore")))
            conn.commit()
            print(msg.topic, msg.payload.decode("utf-8","ignore"))
            return

        # ---- data topic → measurements table ----
        if msg.topic == topics["data"]:
            try:
                d = json.loads(msg.payload.decode("utf-8","ignore"))
            except Exception as e:
                # If the payload is not valid JSON, skip but report the issue.
                print("bad json:", e)
                return

            # Maintain IAQ rolling buffer for quality computation.
            iaq = d.get("iaq"); acc = d.get("iaq_acc")
            if isinstance(iaq, (int, float)):
                iaq_buf.append(float(iaq))
                # Keep most recent 120 samples (~2 minutes @1Hz) to bound memory.
                if len(iaq_buf) > 120:
                    iaq_buf[:] = iaq_buf[-120:]
            qflag = compute_quality(acc, iaq_buf)

            # Persist a single measurement row (missing values stored as NULL).
            conn.execute("""INSERT INTO measurements
                (exp_id, ts_iso, iaq, iaq_accuracy, b_voc_eq_ppm, eco2_ppm,
                 temp_C, rh_pct, press_hPa, quality_flag)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (exp_id, ts, d.get("iaq"), acc, d.get("bvoc_ppm"), d.get("eco2_ppm"),
                 d.get("temp_C"), d.get("rh_pct"), d.get("press_hPa"), qflag))
            conn.commit()

            # Minimal console readout to track core channels in real time.
            print("data", iso_now(),
                  "temp=", d.get("temp_C"),
                  "rh=",   d.get("rh_pct"),
                  "press_hPa=", d.get("press_hPa"))

    # ---- Build MQTT client, attach callbacks, connect, loop forever ----
    client = mqtt.Client()            # auto-generated client_id
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, keepalive=60)

    print("Listening on:", BROKER, "port:", PORT)
    print("Topic prefix:", f"devices/{device_id}/#")
    # loop_forever handles I/O, keepalive pings, and automatic reconnects.
    client.loop_forever()

if __name__ == "__main__":
    main()
