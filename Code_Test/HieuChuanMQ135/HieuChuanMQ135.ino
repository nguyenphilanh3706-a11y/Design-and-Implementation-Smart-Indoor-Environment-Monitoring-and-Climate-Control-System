#include <Arduino.h>

#define AO_PIN 4      
#define VCC 5.0
#define RL 2000.0      // 202 = 2 kΩ
#define R0 3200.0     

void setup() {
  Serial.begin(115200);

  analogReadResolution(12); 
}

void loop() {
  const int N = 50;
  long sum = 0;
  for (int i = 0; i < N; i++) {
    sum += analogRead(AO_PIN);
    delay(5);
  }
  float adc = sum / (float)N;
  float Vadc = adc * 3.3 / 4095.0;
  float Vao = Vadc * 2;
  float Rs = ((VCC - Vao) / Vao) * RL;
  float ratio = Rs / R0;

  Serial.println("--------------------");
  Serial.print("ADC       : ");
  Serial.println(adc);

  Serial.print("V_ADC     : ");
  Serial.print(Vadc, 3);
  Serial.println(" V");

  Serial.print("V_AO      : ");
  Serial.print(Vao, 3);
  Serial.println(" V");

  Serial.print("Rs        : ");
  Serial.print(Rs, 1);
  Serial.println(" ohm");

  Serial.print("Rs/R0     : ");
  Serial.println(ratio, 3);

  delay(1000);
}