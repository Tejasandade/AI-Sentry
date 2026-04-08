#include <ESP32Servo.h>

Servo servo1;
Servo servo2;

// The exact pins we wired up on the breadboard
const int servo1Pin = 13; // Row 5, Column j
const int servo2Pin = 14; // Row 8, Column j

void setup() {
  Serial.begin(115200);
  
  // Standard ESP32 timer allocation for servos
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  
  servo1.setPeriodHertz(50); // Standard 50Hz servo
  servo2.setPeriodHertz(50); 

  // MG995 typical min/max pulse widths are 500 and 2400
  servo1.attach(servo1Pin, 500, 2400); 
  servo2.attach(servo2Pin, 500, 2400);
}

void loop() {
  Serial.println("Moving to 0 degrees");
  servo1.write(0);
  servo2.write(0);
  delay(2000);

  Serial.println("Moving to 90 degrees");
  servo1.write(90);
  servo2.write(90);
  delay(2000);
  
  Serial.println("Moving to 180 degrees");
  servo1.write(180);
  servo2.write(180);
  delay(2000);
}
