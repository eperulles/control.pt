#include <SPI.h>
#include <Adafruit_MAX31856.h>
#include <Preferences.h>
#include <WiFi.h>

// --- WIFI CREDENTIALS - UPDATE THESE ---
const char* ssid     = "WasionWLAN";
const char* password = "W@$1on2021$#";

WiFiServer server(8080);

// --- HARDWARE DEFS ---
#define MAX_SCK  18
#define MAX_MISO 19
#define MAX_MOSI 23
#define MAX_CS   5

Adafruit_MAX31856 sensor = Adafruit_MAX31856(MAX_CS);

// Coeficientes calculados (regresión)
const float GAIN   = 2.430492135971587;
const float OFFSET = -120.7828513444948;

void setup() {
  Serial.begin(115200);
  SPI.begin(MAX_SCK, MAX_MISO, MAX_MOSI);

  // --- SENSOR SETUP ---
  if (!sensor.begin()) {
    Serial.println("No se detecta MAX31856");
    while (1) delay(500);
  }
  sensor.setThermocoupleType(MAX31856_TCTYPE_K);

  // --- WIFI SETUP ---
  Serial.println();
  Serial.print("Conectando a ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("WiFi conectada.");
  Serial.println("Dirección IP: ");
  Serial.println(WiFi.localIP());

  server.begin();
  Serial.println("Servidor TCP iniciado en puerto 8080");
}

void loop() {
  WiFiClient client = server.available(); // Listen for incoming clients

  if (client) {
    Serial.println("Nuevo Cliente conectado.");
    while (client.connected()) {
      // Read Temperature
      float raw = sensor.readThermocoupleTemperature();
      float cj  = sensor.readCJTemperature();
      uint8_t f = sensor.readFault();
      float corrected = raw * GAIN + OFFSET;

      // Format String
      // Matches Python Parser: Raw: 26.50 degC	Corr: 26.30 degC	CJ: 27.00 degC	Fault: 0
      String dataLine = "Raw: " + String(raw) + " degC\tCorr: " + String(corrected) + " degC\tCJ: " + String(cj) + " degC\tFault: " + String(f);
      
      // Send to WiFi Client
      client.println(dataLine);
      
      // Also print to Serial for debug
      Serial.println(dataLine);

      delay(700); // 700ms sampling rate
    }
    client.stop();
    Serial.println("Cliente desconectado.");
  }
}
