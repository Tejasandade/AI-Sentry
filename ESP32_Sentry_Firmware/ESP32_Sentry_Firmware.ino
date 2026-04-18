#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiUdp.h>
#include <WebServer.h>
#include <ESP32Servo.h>

WiFiMulti wifiMulti;
WiFiUDP udp;

const unsigned int UDP_PORT = 8888;

// --- Hardware Setup ---
Servo panServo;
Servo tiltServo;

// REMINDER: Make sure your Tilt servo wire is moved from GPIO 13 to GPIO 27!
const int panPin = 14;  // GPIO 14
const int tiltPin = 27; // GPIO 27
const int greenLED = 12; // Access Granted
const int redLED = 13;   // Access Denied (Intruder)

// --- Cinematic Smoothing Engine ---
int currentPan = 90;
int currentTilt = 90;
int targetPan = 90;
int targetTilt = 90;

// --- Web Server Setup ---
WebServer server(80);

void handleControl() {
  if (server.hasArg("pan")) {
    targetPan = constrain(server.arg("pan").toInt(), 0, 180);
  }
  
  if (server.hasArg("tilt")) {
    targetTilt = constrain(server.arg("tilt").toInt(), 0, 180);
  }
  
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handlePing() {
  server.send(200, "application/json", "{\"status\":\"sentry_alive\"}");
}

void setup() {
  Serial.begin(115200);

  // Security Verification LEDs
  pinMode(greenLED, OUTPUT);
  pinMode(redLED, OUTPUT);
  digitalWrite(greenLED, LOW);
  digitalWrite(redLED, LOW);

  // Servo Timers (ESP32 Specific)
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  
  panServo.setPeriodHertz(50);
  tiltServo.setPeriodHertz(50);
  
  panServo.attach(panPin, 500, 2500);
  tiltServo.attach(tiltPin, 500, 2500);

  // Center the servos instantly on boot
  panServo.write(currentPan);
  tiltServo.write(currentTilt);

  // Critical fix for switching networks dynamically
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true);
  delay(100);

  // Auto-Connection List: It will magically pick whichever is nearby
  wifiMulti.addAP("Dracarys", "Vhagar1210");  // Multi-Net: Home
  wifiMulti.addAP("Galaxy", "11111212");      // Multi-Net: College Hotspot

  Serial.println("Connecting to WiFi...");
  while (wifiMulti.run() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWiFi Connected!");
  Serial.print("Connected to ID: ");
  Serial.println(WiFi.SSID());
  Serial.print("ESP32 IP Address: ");
  Serial.println(WiFi.localIP());

  // Setup API Endpoint
  server.on("/control", HTTP_GET, handleControl);
  server.on("/ping", HTTP_GET, handlePing);
  server.begin();
  Serial.println("HTTP Server Started!");

  udp.begin(UDP_PORT);
}

void loop() {
  server.handleClient();
  
  // Maintains connection dynamically if network drops
  wifiMulti.run();

  // ----- CINEMATIC SERVO SMOOTHING (15ms Glide) -----
  static unsigned long lastMove = 0;
  if(millis() - lastMove > 15) {
     if(currentPan < targetPan) { currentPan++; panServo.write(currentPan); }
     if(currentPan > targetPan) { currentPan--; panServo.write(currentPan); }
     
     if(currentTilt < targetTilt) { currentTilt++; tiltServo.write(currentTilt); }
     if(currentTilt > targetTilt) { currentTilt--; tiltServo.write(currentTilt); }
     
     lastMove = millis();
  }

  // ----- INBOUND UDP SENTRY COMMANDS (High-Speed Lag-Free) -----
  int packetSize = udp.parsePacket();
  if (packetSize) {
    char incoming[50]; // We only expect short strings like P:90,T:90
    int len = udp.read(incoming, 49);
    if (len > 0) {
      incoming[len] = 0;
      String data = String(incoming);
      
      // Expected Format: "P:90,T:90,S:KNOWN"
      if(data.startsWith("P:") && data.indexOf(",T:") > 0) {
        int pIndex = 2;
        int commaIndex = data.indexOf(",T:");
        int tIndex = commaIndex + 3;
        int sIndex = data.indexOf(",S:");
        
        if (sIndex > 0) {
          targetPan = constrain(data.substring(pIndex, commaIndex).toInt(), 0, 180);
          targetTilt = constrain(data.substring(tIndex, sIndex).toInt(), 0, 180);
          
          String state = data.substring(sIndex + 3);
          if (state == "KNOWN") {
             digitalWrite(greenLED, HIGH);
             digitalWrite(redLED, LOW);
          } else if (state == "UNKNOWN" || state == "FIRING") {
             digitalWrite(greenLED, LOW);
             digitalWrite(redLED, HIGH);
          } else {
             digitalWrite(greenLED, LOW);
             digitalWrite(redLED, LOW);
          }
        } else {
          // Fallback if state is missing
          targetPan = constrain(data.substring(pIndex, commaIndex).toInt(), 0, 180);
          targetTilt = constrain(data.substring(tIndex).toInt(), 0, 180);
        }
      }
    }
  }

  // ----- ZERO-TOUCH AUTO-DISCOVERY BEACON -----
  // Shouts to the network every 1.5 seconds so Python can find it
  static unsigned long lastBroadcast = 0;
  if (millis() - lastBroadcast > 1500) {
    // 255.255.255.255 is the universal broadcast IP
    udp.beginPacket(IPAddress(255, 255, 255, 255), UDP_PORT);
    udp.print("ESP32_SENTRY");
    udp.endPacket();
    lastBroadcast = millis();
  }
}