#include <ESP32Servo.h>

Servo myServo;

// We plugged the Brown wire (Signal) into Row 8, which is GPIO 14
const int servoPin = 14; 

void setup() {
  Serial.begin(115200);
  
  // Recommended for newer ESP32 Arduino Core versions
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  
  // Standard standard frequency for MG995 is 50Hz
  myServo.setPeriodHertz(50);      
  // Attach on pin 14, min/max pulse width for MG995 servo 
  myServo.attach(servoPin, 500, 2500); 
  
  Serial.println("Servo Test Started!");
}

void loop() {
  // -------------------------------------------------------------------
  // FOR A 360 CONTINUOUS ROTATION SERVO:
  // 90 = Stop (Though sometimes it might "creep" and need 89 or 91)
  // 180 = Full Speed Forward (or backward depending on wiring)
  // 0 = Full Speed Backward
  // -------------------------------------------------------------------

  Serial.println("Spinning Full Speed One Way...");
  myServo.write(180); 
  delay(2000);

  Serial.println("Stopping...");
  myServo.write(90);
  delay(2000);

  Serial.println("Spinning Full Speed The Other Way...");
  myServo.write(0);
  delay(2000);
  
  Serial.println("Stopping...");
  myServo.write(90);
  delay(3000);
}
