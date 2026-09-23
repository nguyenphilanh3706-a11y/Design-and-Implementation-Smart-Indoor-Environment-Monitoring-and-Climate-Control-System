/*
 * test_kitchen_monitor.ino  -  ESP32 DevKit (Arduino core 2.x hoặc 3.x)
 *
 * Test: AHT20 (nhiệt độ, độ ẩm) + MQ135 (chất lượng không khí) + LCD 1602 I2C
 *       + buzzer báo động khi > 1000 ppm + quạt PWM chạy theo chất lượng không khí
 *
 * LCD luân phiên mỗi 2 s:
 *   Màn 1:  Temp  : 30.5°C          Màn 2:  Air   :  412 ppm
 *           Humid : 65.2 %                  Status: GOOD
 *
 * Thư viện (Arduino IDE > Library Manager):
 *   - Adafruit AHTX0     (bấm "Install all" để cài kèm Adafruit BusIO + Adafruit Unified Sensor)
 *   - LiquidCrystal I2C  (Frank de Brabander)
 *
 * Serial Monitor 115200: gõ c + Enter để hiệu chuẩn lại R0 của MQ135
 * (chỉ làm khi đang ở không khí sạch và MQ135 đã chạy vài phút).
 */

//#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <DIYables_LCD_I2C.h>
#include "esp_arduino_version.h"

// ======================= SƠ ĐỒ CHÂN =======================
#define PIN_MQ_AO     34      // MQ135 A0 (ADC1)
#define PIN_MQ_DO     27      // MQ135 D0: LOW = vượt ngưỡng chỉnh bằng biến trở trên module
#define PIN_BUZZER    23
#define PIN_SDA       21      // I2C dùng chung: AHT20 (0x38) + LCD (0x27)
#define PIN_SCL       22
#define PIN_FAN       26      // PWM -> MOSFET quạt    

// ======================= NGƯỠNG =======================
#define PPM_MID       800     // từ 800 ppm   -> MID
#define PPM_ALARM     1000    // trên 1000 ppm -> BAD + báo động
#define PPM_HYST      50      // chỉ tắt báo động khi xuống dưới 950 ppm (tránh kêu chập chờn)

// ======================= THỜI GIAN =======================
#define PAGE_MS       2000    // mỗi màn LCD hiển thị 2 s
#define SENSOR_MS     1000    // đọc cảm biến mỗi 1 s
#define BEEP_MS       250     // nhịp bíp khi báo động
#define WARMUP_S      30      // làm nóng MQ135 lúc khởi động (giây)

// ======================= BUZZER =======================
#define BUZZER_ON     HIGH    // module buzzer kích mức thấp (kêu khi chân = LOW) thì đổi thành LOW
#define BUZZER_OFF    (!BUZZER_ON)

// ======================= QUẠT PWM =======================
#define FAN_FREQ      5000    // Hz. Quạt kêu rít -> thử 20000; module MOSFET có opto -> giảm còn ~1000
#define FAN_RES       8       // 8 bit: duty 0..255
#define FAN_CH        0       // kênh LEDC (chỉ dùng cho core 2.x)
#define FAN_GOOD      0       // GOOD: tắt
#define FAN_MID       150     // MID : ~60%
#define FAN_BAD       255     // BAD : 100%

// ======================= MQ135 =======================
#define MQ_VCC        5.0     // điện áp cấp cho module MQ135 (V)
#define MQ_DIV        1.5     // V_A0 = V_chân34 x MQ_DIV. Cầu 10k nối tiếp + 20k xuống GND -> 1.5; nối thẳng -> 1.0
#define MQ_RL         10.0    // kΩ, điện trở tải trên module (không ảnh hưởng ppm vì R0 cũng tính với RL này)
#define MQ_R0         0.0     // kΩ. 0 = tự hiệu chuẩn lúc khởi động. Có R0 chuẩn (in ra Serial) thì điền vào đây
#define ATMO_CO2      420.0   // ppm CO2 của không khí sạch, làm mốc hiệu chuẩn
#define MQ_PARA       116.6020682   // đường cong CO2 của MQ135: ppm = PARA * (Rs/R0)^(-PARB)
#define MQ_PARB       2.769034857

// ======================= BIẾN TOÀN CỤC =======================
Adafruit_AHTX0    aht;
DIYables_LCD_I2C lcd(0x27, 16, 2);

enum AirStatus { AIR_GOOD, AIR_MID, AIR_BAD };
const char *STATUS_TXT[] = { "GOOD", "MID", "BAD" };

bool      ahtOk      = false;
float     tempC      = NAN, humi = NAN;
float     mqVolt     = 0, mqRs = 0, r0 = 1, ppm = 0;
int       mqDO       = HIGH;
AirStatus airStatus  = AIR_GOOD;
bool      alarmOn    = false;
uint8_t   fanDuty    = 0;
uint8_t   page       = 0;
uint32_t  lastSensor = 0, lastPage = 0;

// ======================= LCD =======================
// In 1 dòng, tự đệm khoảng trắng cho đủ 16 ký tự để đè chữ cũ (không cần lcd.clear -> không nháy)
void lcdLine(uint8_t row, const char *text) {
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", text);
  lcd.setCursor(0, row);
  lcd.print(buf);
}

// ======================= QUẠT (tương thích core 2.x & 3.x) =======================
void fanBegin() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(PIN_FAN, FAN_FREQ, FAN_RES);
#else
  ledcSetup(FAN_CH, FAN_FREQ, FAN_RES);
  ledcAttachPin(PIN_FAN, FAN_CH);
#endif
}

void fanWrite(uint8_t duty) {
  fanDuty = duty;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(PIN_FAN, duty);
#else
  ledcWrite(FAN_CH, duty);
#endif
}

// ======================= I2C SCAN =======================
// Đúng dây thì phải thấy 0x27 (LCD) và 0x38 (AHT20)
void i2cScan() {
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[I2C] Found 0x%02X\n", addr);
      found++;
    }
  }
  if (!found) Serial.println("[I2C] No device -> check SDA/SCL/VCC/GND");
}

// ======================= AHT20 =======================
void readAHT() {
  if (!ahtOk) return;
  sensors_event_t h, t;
  if (aht.getEvent(&h, &t)) {
    tempC = t.temperature;
    humi  = h.relative_humidity;
  } else {
    tempC = humi = NAN;
  }
}

// ======================= MQ135 =======================
// Điện áp thật tại chân A0 của module (V), trung bình 32 mẫu cho đỡ nhiễu
float readMQVolt() {
  uint32_t sum = 0;
  for (int i = 0; i < 32; i++) sum += analogReadMilliVolts(PIN_MQ_AO);
  return sum / 32.0 / 1000.0 * MQ_DIV;
}

// Hệ số bù nhiệt độ/độ ẩm (chuẩn hoá về 20°C, 33%RH) - theo thư viện MQ135 của GeorgK
float mqCorrection(float t, float h) {
  if (isnan(t) || isnan(h)) return 1.0;
  if (t < 20) return 0.00035 * t * t - 0.02718 * t + 1.39538 - (h - 33.0) * 0.0018;
  return -0.003333333 * t - 0.001923077 * h + 1.130128205;
}

// Điện trở cảm biến Rs (kΩ), đã bù nhiệt ẩm
float calcRs(float v) {
  v = constrain(v, 0.01, MQ_VCC - 0.01);
  return MQ_RL * (MQ_VCC - v) / v / mqCorrection(tempC, humi);
}

// Tìm R0 sao cho không khí hiện tại = ATMO_CO2 ppm (phải đang ở không khí sạch)
float calibrateR0() {
  float sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += calcRs(readMQVolt());
    delay(100);
  }
  return (sum / 20) * pow(ATMO_CO2 / MQ_PARA, 1.0 / MQ_PARB);
}

void readAir() {
  mqVolt  = readMQVolt();
  mqRs    = calcRs(mqVolt);
  float p = MQ_PARA * pow(mqRs / r0, -MQ_PARB);
  ppm     = constrain(p, 0, 9999);
  mqDO    = digitalRead(PIN_MQ_DO);
}

// ======================= TRẠNG THÁI + QUẠT =======================
void updateStatus() {
  bool wasOn = alarmOn;
  if (ppm > PPM_ALARM)                 alarmOn = true;
  else if (ppm < PPM_ALARM - PPM_HYST) alarmOn = false;

  if (alarmOn)             airStatus = AIR_BAD;
  else if (ppm >= PPM_MID) airStatus = AIR_MID;
  else                     airStatus = AIR_GOOD;

  if (alarmOn != wasOn) Serial.println(alarmOn ? "[ALARM] ON - air quality BAD" : "[ALARM] OFF");

  fanWrite(airStatus == AIR_BAD ? FAN_BAD : airStatus == AIR_MID ? FAN_MID : FAN_GOOD);
}

// Buzzer bíp ngắt quãng khi báo động (không dùng delay -> LCD vẫn chạy bình thường)
void updateBuzzer() {
  static uint32_t last = 0;
  static bool on = false;
  if (!alarmOn) {
    if (on) { on = false; digitalWrite(PIN_BUZZER, BUZZER_OFF); }
    return;
  }
  if (millis() - last >= BEEP_MS) {
    last = millis();
    on = !on;
    digitalWrite(PIN_BUZZER, on ? BUZZER_ON : BUZZER_OFF);
  }
}

// ======================= HIỂN THỊ =======================
void drawPage() {
  char l1[17], l2[17];
  if (page == 0) {                               // Màn 1: nhiệt độ + độ ẩm
    if (isnan(tempC)) {
      snprintf(l1, sizeof(l1), "Temp  : --.-%cC", 223);
      snprintf(l2, sizeof(l2), "Humid : --.- %%");
    } else {
      snprintf(l1, sizeof(l1), "Temp  : %4.1f%cC", tempC, 223);   // 223 = ký tự ° của LCD
      snprintf(l2, sizeof(l2), "Humid : %4.1f %%", humi);
    }
  } else {                                       // Màn 2: chỉ số + tình trạng không khí
    snprintf(l1, sizeof(l1), "Air   : %4d ppm", (int)ppm);
    snprintf(l2, sizeof(l2), "Status: %s", STATUS_TXT[airStatus]);
  }
  lcdLine(0, l1);
  lcdLine(1, l2);
}

void printSerial() {
  Serial.printf("T=%.1fC H=%.1f%% | A0=%.2fV Rs=%.1fk Rs/R0=%.2f | %4d ppm %-4s | D0=%s | Fan=%d%%\n",
                tempC, humi, mqVolt, mqRs, mqRs / r0, (int)ppm, STATUS_TXT[airStatus],
                mqDO == LOW ? "LOW" : "HIGH", fanDuty * 100 / 255);
}

// Gõ 'c' trên Serial Monitor để hiệu chuẩn lại R0
void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'c' || c == 'C') {
      digitalWrite(PIN_BUZZER, BUZZER_OFF);
      lcdLine(0, "Calibrating R0..");
      lcdLine(1, "Keep air clean");
      r0 = calibrateR0();
      Serial.printf("[MQ135] New R0 = %.2f kOhm -> copy into MQ_R0\n", r0);
    }
  }
}

// ======================= SETUP =======================
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== Kitchen monitor - sensor test ===");

  pinMode(PIN_BUZZER, OUTPUT);
  digitalWrite(PIN_BUZZER, BUZZER_OFF);
  pinMode(PIN_MQ_DO, INPUT_PULLUP);
  fanBegin();
  fanWrite(0);

  Wire.begin(PIN_SDA, PIN_SCL);
  i2cScan();

  lcd.init();
  lcd.backlight();
  lcdLine(0, "Kitchen Monitor");
  lcdLine(1, "Sensor test...");

  ahtOk = aht.begin(&Wire);
  Serial.println(ahtOk ? "[AHT20] OK" : "[AHT20] NOT FOUND (0x38)");

  // Tự kiểm tra: bíp 1 tiếng + quạt chạy 1 s
  digitalWrite(PIN_BUZZER, BUZZER_ON);
  delay(150);
  digitalWrite(PIN_BUZZER, BUZZER_OFF);
  fanWrite(FAN_BAD);
  delay(1000);
  fanWrite(0);

  // Làm nóng MQ135, trong lúc chờ vẫn hiện nhiệt độ/độ ẩm để test AHT20
  for (int s = WARMUP_S; s > 0; s--) {
    readAHT();
    char l1[17], l2[17];
    snprintf(l1, sizeof(l1), "MQ warm up: %2ds", s);
    if (isnan(tempC)) snprintf(l2, sizeof(l2), "AHT20 error");
    else              snprintf(l2, sizeof(l2), "T:%.1f%cC H:%.0f%%", tempC, 223, humi);
    lcdLine(0, l1);
    lcdLine(1, l2);
    delay(1000);
  }

  if (MQ_R0 > 0) {
    r0 = MQ_R0;
  } else {
    lcdLine(0, "Calibrating R0..");
    lcdLine(1, "Keep air clean");
    r0 = calibrateR0();
    Serial.printf("[MQ135] R0 = %.2f kOhm -> copy into MQ_R0 to skip auto-calibration\n", r0);
  }
  Serial.println("Type 'c' + Enter to re-calibrate R0 (clean air only).");

  lastPage   = millis();
  lastSensor = millis() - SENSOR_MS;   // đọc cảm biến ngay ở vòng loop đầu tiên
}

// ======================= LOOP =======================
void loop() {
  uint32_t now = millis();

  if (now - lastSensor >= SENSOR_MS) {
    lastSensor = now;
    readAHT();
    readAir();
    updateStatus();
    printSerial();
    drawPage();                  // cập nhật số mới lên màn đang hiện
  }

  if (now - lastPage >= PAGE_MS) {
    lastPage = now;
    page ^= 1;                   // đổi màn 0 <-> 1
    drawPage();
  }

  updateBuzzer();
  handleSerial();
}
