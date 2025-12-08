#include <SPI.h>
#include <Adafruit_MAX31856.h>
#include <Preferences.h>
#include <WiFi.h>

// --- CREDECIALES WIFI ---
// Cambia esto por el nombre y contraseña de tu módem/router
const char* ssid     = "NOMBRE_DE_TU_RED";
const char* password = "TU_CONTRASEÑA";

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

  // Esperar conexión
  int intentos = 0;
  while (WiFi.status() != WL_CONNECTED && intentos < 20) {
    delay(500);
    Serial.print(".");
    intentos++;
  }

  if (WiFi.status() == WL_CONNECTED) {
      Serial.println("");
      Serial.println("WiFi conectada.");
      Serial.println("Dirección IP del ESP32: ");
      Serial.println(WiFi.localIP());
      Serial.println("--> PON ESTA IP EN LA APP DE PYTHON <--");
  } else {
      Serial.println("\nNo se pudo conectar al WiFi. Verifica nombre y contraseña.");
  }

  server.begin();
}

void loop() {
  WiFiClient client = server.available(); // Escuchar clientes

  if (client) {
    Serial.println("App conectada.");
    while (client.connected()) {
      // Leer Temperatura
      float raw = sensor.readThermocoupleTemperature();
      float cj  = sensor.readCJTemperature();
      uint8_t f = sensor.readFault();
      float corrected = raw * GAIN + OFFSET;

      // Formato compatible con el parser de Python
      String dataLine = "Raw: " + String(raw) + " degC\tCorr: " + String(corrected) + " degC\tCJ: " + String(cj) + " degC\tFault: " + String(f);
      
      client.println(dataLine);
      delay(700); 
    }
    client.stop();
    Serial.println("App desconectada.");
  }
}
