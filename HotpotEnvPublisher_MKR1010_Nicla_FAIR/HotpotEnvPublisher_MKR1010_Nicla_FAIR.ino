// HotpotEnvPublisher_MKR1010_Nicla_FAIR.ino
//
// FAIR-style annotated sketch for MKR WiFi 1010 + Nicla Sense ME (via ESLOV).
// Purpose: publish environmental telemetry (temperature, relative humidity,
//          barometric pressure) to MQTT at 1 Hz with a 15 s heartbeat.
//
// ─────────────────────────────────────────────────────────────────────────────
// FINDABLE
//   Title: Hotpot Environment Publisher (MKR1010 + Nicla Sense ME)
//   Keywords: hotpot, kitchen, temperature, humidity, pressure, MQTT, Nicla ME
//   Version: 1.0 (2025-12-08)
//   Contact: <your@email>
//   Repo/DOI: add a URL/DOI here when archived
//
// ACCESSIBLE
//   Transport: MQTT over TCP (WiFiNINA)
//   Broker: test.mosquitto.org:1883 (public; use a private broker for real use)
//   Topics (all strings, JSON payloads):
//     devices/<DEVICE_ID>/status   (retained, "warming_up" → "ready")
//     devices/<DEVICE_ID>/heartbeat  {"ms": <uptime_ms>}
//     devices/<DEVICE_ID>/data       {"temp_C": <°C|null>, "rh_pct": <%(0-100)|null>,
//                                     "press_hPa": <hPa|null>, "ms": <uptime_ms>}
//   QoS/retain: status is retained (so new subscribers learn current state);
//               data/heartbeat are non-retained.
//
// INTEROPERABLE
//   Units: SI (°C, %, hPa). Time base is monotonic uptime in milliseconds.
//   Sampling: 1 Hz for data; 15 s for heartbeat.
//   JSON schema (informal):
//     temp_C:    number|null   // −40..85 °C typical; 0 is treated INVALID
//     rh_pct:    number|null   // 0.5..100 %
//     press_hPa: number|null   // 300..1100 hPa
//     ms:        integer       // milliseconds since setup()
//   Time alignment for analysis: align per external event "start_cook".
//
// REUSABLE
//   Provenance/quality rules (see normalization helpers below):
//     • Temperature: raw 0.0 is INVALID (common sensor glitch); values near 0 °C
//       are also considered invalid for this use case; typical range −40..85 °C.
//     • RH: plausible 0.5..100 %; tries simple scale fixes (×100, ÷10, ÷100).
//     • Pressure: accepts only 300..1100 hPa; auto converts Pa→hPa if >2000.
//   Gated start: publishing begins only after N consecutive valid samples
//     (READY_CONSEC_N) for all three channels.
//   Self-heal: if a channel is invalid repeatedly, re-begin() that sensor stream
//     and temporarily substitute last_good_* to maintain structure.
//   Power note: LED is toggled to keep minimal load for some power banks.
//
//   License: add your preferred license here (e.g., MIT; SPDX: MIT).
// ─────────────────────────────────────────────────────────────────────────────
//
// Dependencies (Arduino Library Manager):
//   • WiFiNINA
//   • ArduinoMqttClient
//   • Arduino_BHY2Host (Nicla Sense ME host driver)
//
// Hardware:
//   • Arduino MKR WiFi 1010
//   • Arduino Nicla Sense ME connected via ESLOV cable
//
// Network security: credentials are hard-coded for a demo. For production,
// move them to a separate header or secure store.
//
// Publishing cadence summary:
//   • Heartbeat: every 15 s
//   • Telemetry: every 1 s (gated until sensors are "ready")
//
// The code below is your original sketch; only comments were added.
// ─────────────────────────────────────────────────────────────────────────────

// MinimalEnv_GatedStart_FIX_TEMP0.ino
// MKR WiFi 1010 + Nicla Sense ME (ESLOV)
// Libs: WiFiNINA, ArduinoMqttClient, Arduino_BHY2Host
// Publishes temp_C, rh_pct, press_hPa @1 Hz; heartbeat @15 s
// Fix: treat temp==0 as INVALID; gated start + self-heal.

#include <Arduino.h>
#include <WiFiNINA.h>
#include <ArduinoMqttClient.h>
#include <Arduino_BHY2Host.h>

// ---- Wi-Fi ----
char ssid[] = "WIFINAME";                 // Demo SSID (replace for production)
char pass[] = "PASSWORD";               // Demo password

// ---- MQTT ----
WiFiClient net;
MqttClient mqtt(net);
const char* MQTT_HOST = "test.mosquitto.org"; // Public test broker
const int   MQTT_PORT = 1883;                 // Plain TCP (no TLS in this demo)

#define DEVICE_ID "mkr-kitchen-01"     // Change per device to avoid topic clashes
String T_STATUS = String("devices/") + DEVICE_ID + "/status";
String T_HEART  = String("devices/") + DEVICE_ID + "/heartbeat";
String T_DATA   = String("devices/") + DEVICE_ID + "/data";

// ---- Timing ----
const unsigned long HEARTBEAT_MS  = 15000UL;  // Heartbeat interval
const unsigned long DATA_MS       = 1000UL;   // Telemetry interval
const unsigned long LED_TOGGLE_MS = 800UL;    // LED blink for power bank keep-alive
unsigned long tBeat=0, tData=0, tLED=0, t0_ms=0;

// ---- Robust start ----
const unsigned long NICLA_BOOT_MS   = 1500UL; // Allow Nicla/power to settle
const unsigned long BARO_WARMUP_MS  = 3000UL; // Barometer warmup
const int READY_CONSEC_N = 5;                 // Require N consecutive valid samples

bool ready=false;                             // Publishing gate
int ok_t=0, ok_rh=0, ok_p=0;                  // Counters for readiness

// ---- Self-heal ----
int bad_temp=0, bad_hum=0, bad_baro=0;        // Consecutive invalid counters
float last_good_t=NAN, last_good_rh=NAN, last_good_p=NAN; // Hold last valid

// ---- Sensors ----
Sensor temperature(SENSOR_ID_TEMP);           // Nicla ME virtual sensors
Sensor hum_s (SENSOR_ID_HUM);
Sensor baro_s(SENSOR_ID_BARO);

// ---- Helpers ----
// Print a JSON number or "null" for NaN; avoids schema breakage in subscribers.
inline void jfloat(float v,int d=2){ if(isnan(v)) mqtt.print("null"); else mqtt.print(v,d); }

// Temperature normalization: reject 0 (and near 0) as invalid
// Rationale: some stacks report 0.0 °C or mis-scaled values during boot.
// We also test simple scale/offset hypotheses to recover plausibility.
float normC(float v){
  if(!isfinite(v)) return NAN;
  if(v == 0.0f) return NAN;                         // <- key: raw 0 is invalid
  float cand[4]={v, v/10.0f, v/100.0f, v-273.15f};  // try obvious scale/offset
  for(int i=0;i<4;i++){
    float x=cand[i];
    if(!isfinite(x)) continue;
    if(x>-40 && x<85){
      if(fabs(x) < 0.5f) return NAN;               // also treat ~0 °C as invalid
      return x;
    }
  }
  return NAN;
}

// RH normalization (0.5..100 % valid). Try common scaling mistakes.
float normRH(float v){
  if(!isfinite(v)) return NAN;
  float cand[4]={v, v/10.0f, v/100.0f, v*100.0f};
  for(int i=0;i<4;i++){
    float x=cand[i];
    if(isfinite(x) && x>=0.5f && x<=100.0f) return x;
  }
  return NAN;
}

float readTempC(){ return normC(temperature.value()); }
float readRH_pct(){ return normRH(hum_s.value()); }
float readPress_hPa(){
  float p=baro_s.value();
  if(!isfinite(p) || p<=0) return NAN;
  if(p>2000.0f) p/=100.0f;            // Convert Pa → hPa when needed
  if(p<300.0f || p>1100.0f) return NAN;
  return p;
}

// Blocking Wi-Fi connect (simple & robust for headless logging)
void wifiConnect(){ while(WiFi.begin(ssid, pass)!=WL_CONNECTED){ delay(3000);} }

// Blocking MQTT connect; publish retained "warming_up"
void mqttConnect(){
  while(!mqtt.connect(MQTT_HOST, MQTT_PORT)){ delay(1000); }
  mqtt.beginMessage(T_STATUS.c_str(), true, 1); mqtt.print("warming_up"); mqtt.endMessage();
}

// Prime BHY2 streams; 5 Hz is ample since we publish at 1 Hz
void beginStreams(){
  for(int i=0;i<20;i++){ BHY2Host.update(); delay(20); }
  temperature.begin(5.0f); hum_s.begin(5.0f); baro_s.begin(5.0f);
}

void setup(){
  delay(NICLA_BOOT_MS);                 // Power/USB banks sometimes need settle time
  // If Nicla is stacked: BHY2Host.begin(false, NICLA_AS_SHIELD);
  BHY2Host.begin();                     // Host driver for Nicla Sense ME
  beginStreams();
  delay(BARO_WARMUP_MS);                // Let barometer stabilize

  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, HIGH);      // Keep minimal load for some power banks

  t0_ms = millis();                     // Monotonic time base
  wifiConnect();
  mqttConnect();
}

void loop(){
  mqtt.poll();                          // Keep MQTT session alive
  BHY2Host.update();                    // Pump sensor FIFO
  unsigned long now=millis();

  // Blink LED as simple "alive" indicator and power-bank keep-alive
  if(now - tLED >= LED_TOGGLE_MS){ tLED=now; digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN)); }

  // Heartbeat every HEARTBEAT_MS
  if(now - tBeat >= HEARTBEAT_MS){
    tBeat=now;
    mqtt.beginMessage(T_HEART.c_str()); mqtt.print("{\"ms\":"); mqtt.print(now); mqtt.print("}"); mqtt.endMessage();
  }

  // Sample sensors each loop (we still publish at 1 Hz below)
  float tC = readTempC();
  float rh = readRH_pct();
  float p  = readPress_hPa();

  // -------- GATED START --------
  // Publish only after all three channels have produced READY_CONSEC_N valid
  // readings, reducing startup spikes from uninitialized sensors.
  if(!ready){
    ok_t  = isnan(tC)? 0 : ok_t+1;
    ok_rh = isnan(rh)? 0 : ok_rh+1;
    ok_p  = isnan(p) ? 0 : ok_p+1;

    // Self-heal during warm-up: if a channel is repeatedly invalid, restart it.
    static int warm_bad_t=0, warm_bad_rh=0, warm_bad_p=0;
    if(isnan(tC) && ++warm_bad_t>=5){ temperature.begin(5.0f); warm_bad_t=0; }
    if(isnan(rh) && ++warm_bad_rh>=5){ hum_s.begin(5.0f);      warm_bad_rh=0; }
    if(isnan(p)  && ++warm_bad_p>=5){ baro_s.begin(5.0f);      warm_bad_p=0; }

    if(ok_t>=READY_CONSEC_N && ok_rh>=READY_CONSEC_N && ok_p>=READY_CONSEC_N){
      ready=true;
      last_good_t=tC; last_good_rh=rh; last_good_p=p;
      mqtt.beginMessage(T_STATUS.c_str(), true, 1); mqtt.print("ready"); mqtt.endMessage();
    }
    return; // Not ready → do not publish DATA yet
  }

  // -------- RUNTIME SELF-HEAL --------
  // If a sensor goes invalid repeatedly, call begin() to re-init it.
  // While healing, substitute last_good_* so downstream consumers keep schema.
  if(isnan(tC)){ if(++bad_temp>=5){ temperature.begin(5.0f); bad_temp=0; } tC=last_good_t; } else { last_good_t=tC; bad_temp=0; }
  if(isnan(rh)){ if(++bad_hum>=5){  hum_s.begin(5.0f);      bad_hum=0; }  rh=last_good_rh; } else { last_good_rh=rh; bad_hum=0; }
  if(isnan(p)){  if(++bad_baro>=5){ baro_s.begin(5.0f);     bad_baro=0; } p=last_good_p; } else { last_good_p=p; bad_baro=0; }

  // Publish data @1 Hz as JSON; use null for missing values
  if(now - tData >= DATA_MS){
    tData=now;
    mqtt.beginMessage(T_DATA.c_str());
    mqtt.print("{\"temp_C\":");    jfloat(tC,2);
    mqtt.print(",\"rh_pct\":");    jfloat(rh,2);
    mqtt.print(",\"press_hPa\":"); jfloat(p,1);
    mqtt.print(",\"ms\":");        mqtt.print(now - t0_ms);
    mqtt.print("}");
    mqtt.endMessage();
  }

  // Simple connectivity guardrails (blocking reconnects)
  if(WiFi.status()!=WL_CONNECTED) wifiConnect();
  if(!mqtt.connected())           mqttConnect();
}
