def get_latlon_from_db_or_city(con, exp_id, postcode="L6 1AJ"):
    """
    Return (latitude, longitude, city_label) for a given experiment.

    Behavior (no side effects changed):
      1) Try to read 'lat', 'lon', and optional 'city' from a key–value table
         named `conditions` with schema like: conditions(exp_id, key, value).
      2) If missing, call the public API postcodes.io to resolve the supplied
         UK postcode to (lat, lon), infer a city-like label, then write those
         keys back into `conditions` (INSERT OR REPLACE) and COMMIT.
      3) On any failure (network error, non-200 status, unexpected JSON), fall
         back to hard-coded Liverpool coordinates and persist those.

    Notes:
      - This function WRITES to the DB when it needs to cache/fall back values.
      - Expects `con` to be a sqlite3.Connection (or compatible) and the
        `conditions` table to exist.
      - postcodes.io usage: https://api.postcodes.io/postcodes/<PC> (free service).
    """
    # Fetch all (key, value) rows for this exp_id, convert to dict for easy lookups.
    kv = dict(con.execute("SELECT key, value FROM conditions WHERE exp_id=?", (exp_id,)))
    if "lat" in kv and "lon" in kv:
        # If we already have cached coordinates, return them immediately.
        # If 'city' is missing, default to "Liverpool, GB" for the label only.
        return float(kv["lat"]), float(kv["lon"]), kv.get("city", "Liverpool, GB")

    try:
        import requests  # Imported lazily to avoid a hard dependency unless needed.

        # Normalize postcode: strip spaces, uppercase (postcodes.io tolerates both).
        pc = postcode.strip().upper().replace(" ", "")

        # Query postcodes.io; 15 s timeout to avoid hanging if the service is slow.
        r = requests.get(f"https://api.postcodes.io/postcodes/{pc}", timeout=15)
        js = r.json()

        # postcodes.io returns a JSON object with 'status' and 'result'.
        if js.get("status") == 200 and js.get("result"):
            res = js["result"]

            # Extract numeric latitude/longitude from the response.
            lat = float(res["latitude"]); lon = float(res["longitude"])

            # Build a friendly city-like label. Prefer admin_district, then parish.
            city_label = (res.get("admin_district") or res.get("parish") or "Liverpool") + ", GB"

            # Cache coordinates and label in the conditions table for future calls.
            con.execute(
                "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
                (exp_id, "lat", str(lat))
            )
            con.execute(
                "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
                (exp_id, "lon", str(lon))
            )
            con.execute(
                "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
                (exp_id, "city", city_label)
            )
            con.commit()

            return lat, lon, city_label

    except Exception as e:
        # Any error (network, JSON, missing fields) falls through to the default.
        # Printing a brief message helps debugging but keeps the pipeline alive.
        print("postcode lookup failed, fallback to default:", e)

    # Fallback: Liverpool city centre–like coordinates, with label "Liverpool, GB".
    lat, lon, city_label = 53.4106, -2.9779, "Liverpool, GB"

    # Persist the fallback so subsequent calls do not keep hitting the API.
    con.execute(
        "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
        (exp_id, "lat", str(lat))
    )
    con.execute(
        "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
        (exp_id, "lon", str(lon))
    )
    con.execute(
        "INSERT OR REPLACE INTO conditions(exp_id,key,value) VALUES(?,?,?)",
        (exp_id, "city", city_label)
    )
    con.commit()

    return lat, lon, city_label

