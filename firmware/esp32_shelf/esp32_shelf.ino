// ScanTrust smart shelf firmware (ESP32 + 4 × HX711 + 4 load cells)
//
// Publishes one MQTT message per pick or put-back:
//   topic   scantrust/shelf/<zone>/event
//   payload {"delta": 1}     (1 unit taken; -1 = put back)
//
// Libraries (Arduino Library Manager): "HX711" by Bogdan Necula, "PubSubClient" by Nick O'Leary.
// Set WIFI_*, MQTT_HOST and the CALIBRATION factors below, then flash.
// Calibrate each zone once: put a known weight on it and adjust CALIBRATION
// until the serial monitor prints that weight in grams.

#include <WiFi.h>
#include <PubSubClient.h>
#include "HX711.h"

const char* WIFI_SSID = "your-wifi";
const char* WIFI_PASS = "your-password";
const char* MQTT_HOST = "192.168.1.10";  // laptop running Mosquitto
const int   MQTT_PORT = 1883;

const int ZONES = 4;
const char* ZONE_ID[ZONES]     = {"A3-Z1", "A3-Z2", "A3-Z3", "A3-Z4"};
const int   DOUT_PIN[ZONES]    = {16, 18, 21, 23};
const int   SCK_PIN[ZONES]     = {17, 19, 22, 25};
const float UNIT_G[ZONES]      = {55.0, 135.0, 380.0, 215.0};  // must match backend catalog
float       CALIBRATION[ZONES] = {420.0, 420.0, 420.0, 420.0};  // tune per load cell

const float STABLE_BAND_G = 6.0;     // readings within this band count as "settled"
const unsigned long SETTLE_MS = 600; // how long the weight must stay settled
const float UNIT_TOLERANCE = 0.35;   // accept 0.65–1.35 × unit weight per unit
const unsigned long IDLE_REZERO_MS = 30000;  // auto re-zero drift after 30 s untouched

HX711 scale[ZONES];
float baseline[ZONES];               // last settled weight that produced an event
float lastReading[ZONES];
unsigned long settledSince[ZONES];
unsigned long lastChange[ZONES];
bool staffMode = false;              // set over MQTT while restocking

WiFiClient net;
PubSubClient mqtt(net);

void onMessage(char* topic, byte* payload, unsigned int len) {
  // scantrust/shelf/staff  payload "on" | "off"  → pause detection while restocking
  String msg;
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  staffMode = (msg == "on");
  if (!staffMode) for (int z = 0; z < ZONES; z++) baseline[z] = scale[z].get_units(5);
  Serial.printf("staff mode %s\n", staffMode ? "ON" : "OFF");
}

void connectAll() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
    Serial.printf("\nWiFi %s\n", WiFi.localIP().toString().c_str());
  }
  while (!mqtt.connected()) {
    if (mqtt.connect("scantrust-shelf-a3")) {
      mqtt.subscribe("scantrust/shelf/staff");
      Serial.println("MQTT connected");
    } else { delay(1000); }
  }
}

void publish(int z, int delta) {
  char topic[48], body[24];
  snprintf(topic, sizeof topic, "scantrust/shelf/%s/event", ZONE_ID[z]);
  snprintf(body, sizeof body, "{\"delta\": %d}", delta);
  mqtt.publish(topic, body);
  Serial.printf("%s delta %d\n", ZONE_ID[z], delta);
}

void setup() {
  Serial.begin(115200);
  for (int z = 0; z < ZONES; z++) {
    scale[z].begin(DOUT_PIN[z], SCK_PIN[z]);
    scale[z].set_scale(CALIBRATION[z]);
    scale[z].tare();                       // empty shelf plate = 0 g; then load stock
    baseline[z] = lastReading[z] = 0;
    settledSince[z] = lastChange[z] = millis();
  }
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
  connectAll();
  delay(3000);                             // time to place stock after tare
  for (int z = 0; z < ZONES; z++) baseline[z] = scale[z].get_units(10);
}

void loop() {
  connectAll();
  mqtt.loop();
  unsigned long now = millis();

  for (int z = 0; z < ZONES; z++) {
    if (!scale[z].is_ready()) continue;
    float w = scale[z].get_units(3);

    // Track whether the reading has settled (a hand resting on the shelf is not an event).
    if (fabs(w - lastReading[z]) > STABLE_BAND_G) { settledSince[z] = now; lastChange[z] = now; }
    lastReading[z] = w;
    if (staffMode || now - settledSince[z] < SETTLE_MS) continue;

    float diff = baseline[z] - w;            // positive = weight removed
    float units = diff / UNIT_G[z];
    int whole = (int)lroundf(units);
    if (whole != 0 && fabs(units - whole) <= UNIT_TOLERANCE * fabs((float)whole)) {
      publish(z, whole);                     // whole > 0 pick, < 0 put back
      baseline[z] = w;
    } else if (whole == 0 && now - lastChange[z] > IDLE_REZERO_MS) {
      baseline[z] = w;                       // slow drift: re-zero while untouched
      lastChange[z] = now;
    }
  }
  delay(50);
}
