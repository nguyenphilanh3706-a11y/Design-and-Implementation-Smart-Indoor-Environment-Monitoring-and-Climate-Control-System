#include <Arduino.h>

#define AO_PIN 4       // ĐỔI thành GPIO ADC của ông
#define VCC 5.0
#define RL 2000.0      // 202 = 2 kΩ
#define R0 3200.0      // R0 ông vừa calibration ~3.2 kΩ

void setup() {
  Serial.begin(115200);

  analogReadResolution(12);  // 0–4095
}

void loop() {
  // Đọc ADC trung bình để giảm nhiễu
  const int N = 50;
  long sum = 0;

  for (int i = 0; i < N; i++) {
    sum += analogRead(AO_PIN);
    delay(5);
  }

  float adc = sum / (float)N;

  // Điện áp tại chân ADC
  float Vadc = adc * 3.3 / 4095.0;

  // Nếu dùng chia áp 10k phía trên + 20k phía dưới:
  // Vadc = Vao * 20/(10+20)
  float Vao = Vadc * 1.5;

  // Tính Rs
  float Rs = ((VCC - Vao) / Vao) * RL;

  // Tỷ lệ Rs/R0
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