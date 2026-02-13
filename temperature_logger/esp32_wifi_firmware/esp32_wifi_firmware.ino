#include <Adafruit_MAX31856.h>
#include <ESPmDNS.h>
#include <SPI.h>
#include <WebServer.h>
#include <WiFi.h>

// --- WIFI ---
const char *ssid = "WasionWLAN";
const char *password = "W@$1on2021$#";

// Servidores
WiFiServer tcpServer(8080);
WebServer httpServer(80);

// --- HARDWARE ---
#define MAX_SCK 18
#define MAX_MISO 19
#define MAX_MOSI 23
#define MAX_CS 5

Adafruit_MAX31856 sensor = Adafruit_MAX31856(MAX_CS);

// Coeficientes Recalculados (Ajuste Final 7 puntos - Incluye 27C)
const float GAIN = 0.788203658308612;
const float OFFSET = 52.63188250923105;

void setup() {
  Serial.begin(115200);
  SPI.begin(MAX_SCK, MAX_MISO, MAX_MOSI);

  // SENSOR
  if (!sensor.begin()) {
    Serial.println("No se detecta MAX31856");
    while (1)
      delay(500);
  }
  sensor.setThermocoupleType(MAX31856_TCTYPE_K);

  // WIFI
  Serial.println("Conectando a WiFi...");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }

  Serial.println("\nWiFi conectada!");
  Serial.print("IP asignada: ");
  Serial.println(WiFi.localIP());

  // ----------------------------------------------------------
  //                  mDNS → http://esp32.local
  // ----------------------------------------------------------
  if (!MDNS.begin("esp32")) {
    Serial.println("mDNS ERROR (no se inició)");
  } else {
    Serial.println("mDNS listo → http://esp32.local");
  }

  // HTTP: solo devuelve la IP en texto plano
  httpServer.on("/", []() {
    httpServer.send(200, "text/plain", WiFi.localIP().toString());
  });

  httpServer.begin();
  Serial.println("HTTP listo");

  tcpServer.begin();
  Serial.println("TCP listo en puerto 8080");
}

void loop() {
  httpServer.handleClient();

  WiFiClient client = tcpServer.available();

  if (client) {
    Serial.println("Cliente TCP conectado");

    while (client.connected()) {
      float rawTemp = sensor.readThermocoupleTemperature();
      float corrected = rawTemp * GAIN + OFFSET;

      // Enviar con 2 decimales
      String dataLine = String(corrected, 2);

      client.println(dataLine);
      Serial.println("Enviado: " + dataLine);

      delay(700);
    }

    client.stop();
    Serial.println("Cliente desconectado");
  }
}
