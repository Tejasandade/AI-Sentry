#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>

// --- WiFi Configuration ---
const char* ssid = "Dracarys";
const char* password = "Vhagar1210";
// --- Hardware Setup ---
Servo panServo;
Servo tiltServo;

const int panPin = 14;  // GPIO 14
const int tiltPin = 13; // GPIO 13

// --- Web Server Setup ---
WebServer server(80);

void handleControl() {
  if (server.hasArg("pan")) {
    int panAngle = server.arg("pan").toInt();
    panAngle = constrain(panAngle, 0, 180);
    panServo.write(panAngle);
  }
  
  if (server.hasArg("tilt")) {
    int tiltAngle = server.arg("tilt").toInt();
    tiltAngle = constrain(tiltAngle, 0, 180);
    tiltServo.write(tiltAngle);
  }
  
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void setup() {
  Serial.begin(115200);

  // Servo Timers (ESP32 Specific)
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  
  panServo.setPeriodHertz(50);
  tiltServo.setPeriodHertz(50);
  
  panServo.attach(panPin, 500, 2500);
  tiltServo.attach(tiltPin, 500, 2500);

  // Center the servos on boot
  panServo.write(90);
  tiltServo.write(90);

  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi Connected!");
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  // Setup API Endpoint
  server.on("/control", HTTP_GET, handleControl);
  server.begin();
  Serial.println("HTTP Server Started up successfully!");
}

void loop() {
  server.handleClient();
}
