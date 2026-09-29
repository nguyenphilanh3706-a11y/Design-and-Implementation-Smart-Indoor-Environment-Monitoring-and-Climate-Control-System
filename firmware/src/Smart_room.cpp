#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <LiquidCrystal_I2C.h>
#include <time.h>
#include "esp_arduino_version.h"
 
// =====================================================================
// 1. CẤU HÌNH MẠNG & MQTT BROKER
// =====================================================================
#define WIFI_SSID     "Galaxy A12 1635"       // <-- điền lại của bạn
#define WIFI_PASSWORD "legy2788"   // <-- điền lại của bạn
 
#define MQTT_HOST     "18.141.107.210"       // <-- điền lại của bạn
const int MQTT_PORT = 1883;
 
const char* MQTT_USER = "esp32_device";
const char* MQTT_PASS = "Esp32S3Pass"; // <-- điền lại của bạn
const char* DEVICE_ID = "esp32_kitchen_01";
 
const char* TOPIC_TELEMETRY      = "iot/kitchen/esp32_kitchen_01/telemetry";
const char* TOPIC_STATUS         = "iot/kitchen/esp32_kitchen_01/status";
const char* TOPIC_ACTUATOR_STATE = "iot/kitchen/esp32_kitchen_01/actuator/state";
const char* TOPIC_ACTUATOR_SET   = "iot/kitchen/esp32_kitchen_01/actuator/set";
const char* TOPIC_MODE_SET       = "iot/kitchen/esp32_kitchen_01/mode/set";
const char* TOPIC_CONFIG_STATE   = "iot/kitchen/esp32_kitchen_01/config/state";
const char* TOPIC_CONFIG_SET     = "iot/kitchen/esp32_kitchen_01/config/set";
 
// =====================================================================
// 2. SƠ ĐỒ CHÂN & NGƯỠNG CẢNH BÁO
// =====================================================================
#define PIN_MQ_AO        34     // MQ135 A0 (ADC1)
#define PIN_MQ_DO        27     // MQ135 D0
#define PIN_FAN          26     // MOSFET Điều khiển quạt PWM
#define PIN_SDA          21     // I2C SDA
#define PIN_SCL          22     // I2C SCL
 
#define PPM_MID          800    // Ngưỡng trung bình
#define PPM_HYST         50     // Trễ hysteresis ppm
#define TEMP_HYST        0.5    // Trễ hysteresis nhiệt độ (độ C)
 
#define PAGE_MS          2000   // Mỗi trang LCD hiện 2 giây
#define SENSOR_MS        1000   // Đọc cảm biến mỗi 1 giây
#define WARMUP_S         15     // Thời gian làm nóng MQ135 khi khởi động (giây)
 
// Cấu hình Quạt PWM (Tương thích Core 2.x & 3.x)
#define FAN_FREQ         5000   // Hz
#define FAN_RES          8      // 8 bit: duty 0..255
#define FAN_CH           0      // Kênh LEDC
#define FAN_GOOD         0      // Tắt quạt
#define FAN_MID          150    // ~60% tốc độ
#define FAN_BAD          255    // 100% tốc độ
#define FAN_SAMPLE_MS    5000   // Khi quạt chạy: cứ 5 giây tạm ngắt quạt để đọc khí
#define FAN_SETTLE_MS    400    // Chờ nhiễu tan sau khi ngắt quạt rồi mới đọc (tăng nếu vẫn lệch)
 
// Cấu hình linh kiện MQ135
#define MQ_VCC           5.0
#define MQ_DIV           1.5    // Hệ số cầu phân áp (vd: 10k/20k => 1.5)
#define MQ_RL            10.0
#define MQ_R0_DEFAULT    10.0   // R0 mặc định (kOhm)
#define ATMO_CO2         420.0
#define MQ_PARA          116.6020682
#define MQ_PARB          2.769034857
 
// =====================================================================
// 3. BIẾN TOÀN CỤC
// =====================================================================
Adafruit_AHTX0 aht;
LiquidCrystal_I2C lcd27(0x27, 16, 2);
LiquidCrystal_I2C lcd3F(0x3F, 16, 2);
LiquidCrystal_I2C* lcd = &lcd27;
 
enum AirStatus { AIR_GOOD, AIR_MID, AIR_BAD };
const char *STATUS_TXT[] = { "GOOD", "MID", "BAD" };
 
bool      ahtOk            = false;
bool      lcdAvailable     = false;
bool      ntpConfigured    = false;
 
float     tempC            = NAN, humi = NAN;
float     mqVolt           = 0, mqRs = 0, r0 = MQ_R0_DEFAULT, ppm = 0;
float     filteredPpm      = 0;
bool      ppmInitialized   = false;   // [SỬA] dùng cờ thay vì so sánh filteredPpm == 0
int       mqDO             = HIGH;
AirStatus airStatus        = AIR_GOOD;
bool      alarmOn          = false;
uint8_t   fanDuty          = 0;
uint8_t   lastReportedDuty = 0;
 
 
int       fanState         = 0;       // 0: OFF, 1: ON
String    currentMode      = "AUTO";  // AUTO / MANUAL
float     pollutionThreshold = 1000.0;
float     tempThreshold      = 33.0;
float     dwellTimeSeconds   = 10.0;  // (chưa được sử dụng trong logic)
 
uint8_t   page             = 0;
uint32_t  lastSensor       = 0;
uint32_t  lastPage         = 0;
uint32_t  lastTelemetryTime= 0;
uint32_t  lastMqttRetryTime= 0;
uint32_t  lastWifiRetryTime= 0;
uint32_t  lastAhtRetryTime = 0;
unsigned long msgSeq       = 0;
 
WiFiClient espClient;
PubSubClient mqttClient(espClient);
 
void sendActuatorState(const char* reason);
void sendConfigState();
void updateStatus();
 
// =====================================================================
// 4. KHỞI TẠO QUẠT PWM & LCD
// =====================================================================
void fanBegin() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(PIN_FAN, FAN_FREQ, FAN_RES);
#else
    ledcSetup(FAN_CH, FAN_FREQ, FAN_RES);
    ledcAttachPin(PIN_FAN, FAN_CH);
#endif
}
 
// Chỉ ghi PWM ra chân, KHÔNG đổi fanDuty/fanState (dùng khi ngắt quạt tạm thời)
void fanHw(uint8_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(PIN_FAN, duty);
#else
    ledcWrite(FAN_CH, duty);
#endif
}
 
void fanWrite(uint8_t duty) {
    fanDuty = duty;
    fanState = (duty > 0) ? 1 : 0;
    fanHw(duty);
}
 
void lcdLine(uint8_t row, const char *text) {
    if (!lcdAvailable) return;
    char buf[17];
    snprintf(buf, sizeof(buf), "%-16s", text);
    lcd->setCursor(0, row);
    lcd->print(buf);
}
 
// =====================================================================
// 5. THUẬT TOÁN ĐỌC CẢM BIẾN (AHT20 & MQ135)
// =====================================================================
void readAHT() {
    if (!ahtOk) {
        if (millis() - lastAhtRetryTime > 10000) {
            lastAhtRetryTime = millis();
            ahtOk = aht.begin(&Wire);
        }
        return;
    }
    sensors_event_t h, t;
    if (aht.getEvent(&h, &t)) {
        tempC = t.temperature;
        humi  = h.relative_humidity;
    } else {
        tempC = humi = NAN;
        ahtOk = false;
    }
}
 
// [SỬA] Đọc thô điện áp A0: lấy 64 mẫu, sắp xếp, bỏ 1/4 đầu và 1/4 cuối
// rồi lấy trung bình (trimmed mean) để loại các mẫu bị gai nhiễu.
float readMQVoltRaw() {
    const int N = 64;
    uint32_t s[N];
    for (int i = 0; i < N; i++) {
        s[i] = analogReadMilliVolts(PIN_MQ_AO);
        delayMicroseconds(300);
    }
    for (int i = 1; i < N; i++) {           // insertion sort
        uint32_t k = s[i];
        int j = i - 1;
        while (j >= 0 && s[j] > k) { s[j + 1] = s[j]; j--; }
        s[j + 1] = k;
    }
    uint32_t sum = 0;
    for (int i = N / 4; i < N - N / 4; i++) sum += s[i];
    float mv = sum / (float)(N / 2);
    return (mv / 1000.0f) * MQ_DIV;
}
 
float readMQVolt() {
    return readMQVoltRaw();
}
 
float mqCorrection(float t, float h) {
    if (isnan(t) || isnan(h)) return 1.0;
    if (t < 20) return 0.00035 * t * t - 0.02718 * t + 1.39538 - (h - 33.0) * 0.0018;
    return -0.003333333 * t - 0.001923077 * h + 1.130128205;
}
 
float calcRs(float v) {
    v = constrain(v, 0.01, MQ_VCC - 0.01);
    return MQ_RL * (MQ_VCC - v) / v / mqCorrection(tempC, humi);
}
 
float calibrateR0() {
    float sum = 0;
    for (int i = 0; i < 20; i++) {
        sum += calcRs(readMQVolt());
        delay(100);
    }
    return (sum / 20) * pow(ATMO_CO2 / MQ_PARA, 1.0 / MQ_PARB);
}
 
void readAir() {
    static uint32_t lastGasSample = 0;
    bool fanRunning = (fanDuty > 0);
    uint32_t nowMs = millis();
 
    // Khi quạt đang chạy: chỉ đọc khí mỗi FAN_SAMPLE_MS, các lần còn lại giữ nguyên ppm cũ
    if (fanRunning && ppmInitialized && (nowMs - lastGasSample < FAN_SAMPLE_MS)) {
        return;
    }
    lastGasSample = nowMs;
 
    // Tạm ngắt quạt để đọc: nhiễu điện biến mất ngay, còn khí thật đổi rất chậm
    if (fanRunning) {
        fanHw(0);
        delay(FAN_SETTLE_MS);
    }
    mqVolt = readMQVoltRaw();
    if (fanRunning) fanHw(fanDuty);   // bật lại quạt đúng tốc độ cũ
    float alpha = fanRunning ? 0.4 : 0.2;   // mẫu thưa hơn nên cho trọng số mẫu mới cao hơn
 
    mqRs   = calcRs(mqVolt);
 
    float p = MQ_PARA * pow(mqRs / r0, -MQ_PARB);
    float rawPpm = constrain(p, 0, 9999);
 
    // Bộ lọc EMA (20% hiện tại, 80% quá khứ)
    if (!ppmInitialized) {
        filteredPpm = rawPpm;
        ppmInitialized = true;
    } else {
        filteredPpm = (alpha * rawPpm) + ((1.0 - alpha) * filteredPpm);
    }
 
    ppm = filteredPpm;
    mqDO = digitalRead(PIN_MQ_DO);
}
 
// [MỚI] Đo độ lệch A0 giữa lúc quạt tắt và bật để kiểm tra nhiễu từ quạt.
void runFanNoiseTest() {
    uint8_t prevDuty = fanDuty;
 
    Serial.println("[TEST] Tat quat, doi 3s de on dinh...");
    fanWrite(FAN_GOOD);
    delay(3000);
    float vOff = readMQVoltRaw();
 
    Serial.println("[TEST] Bat quat 100%, doi 3s...");
    fanWrite(FAN_BAD);
    delay(3000);
    float vOn = readMQVoltRaw();
 
    fanWrite(prevDuty);
    Serial.printf("[TEST] A0 quat TAT = %.3f V | quat BAT = %.3f V | lech = %.3f V\n", vOff, vOn, vOn - vOff);
    Serial.println("[TEST] Lech lon => nhieu tu quat. Firmware da tu ngat quat tam thoi khi doc khi (FAN_SAMPLE_MS / FAN_SETTLE_MS).");
}
 
// =====================================================================
// 6. XỬ LÝ LOGIC QUẠT & TRẠNG THÁI
// =====================================================================
void updateStatus() {
    bool wasOn = alarmOn;
 
    // [SỬA] Xử lý tempC = NaN (AHT20 lỗi): trước đây alarm sẽ không bao giờ tắt được.
    bool tempHigh = !isnan(tempC) && tempC > tempThreshold;
    bool tempSafe = isnan(tempC) || tempC <= tempThreshold - TEMP_HYST;
 
    if (ppm > pollutionThreshold || tempHigh) {
        alarmOn = true;
    } else if (ppm < pollutionThreshold - PPM_HYST && tempSafe) {
        alarmOn = false;
    }
 
    if (alarmOn)              airStatus = AIR_BAD;
    else if (ppm >= PPM_MID)  airStatus = AIR_MID;
    else                      airStatus = AIR_GOOD;
 
    if (currentMode == "AUTO") {
        fanWrite(airStatus == AIR_BAD ? FAN_BAD : airStatus == AIR_MID ? FAN_MID : FAN_GOOD);
        // [MỚI] Báo trạng thái quạt lên dashboard khi chế độ AUTO tự đổi tốc độ
        if (fanDuty != lastReportedDuty) sendActuatorState("AUTO_CONTROL");
    }
 
    if (alarmOn != wasOn) {
        Serial.println(alarmOn ? "[ALARM] ON - Vuot nguong canh bao!" : "[ALARM] OFF - Da an toan");
    }
}
 
void printSerial() {
    Serial.printf(
        "T=%.1fC H=%.1f%% | A0=%.3fV Rs=%.1fk Rs/R0=%.2f | %4d ppm %-4s | D0=%s | "
        "Mode=%-6s Fan=%3d%% (state=%d) | Alarm=%s\n",
        tempC, humi, mqVolt, mqRs, mqRs / r0, (int)ppm, STATUS_TXT[airStatus],
        mqDO == LOW ? "LOW" : "HIGH",
        currentMode.c_str(), (int)(fanDuty * 100 / 255), fanState,
        alarmOn ? "ON" : "OFF");
}
 
// =====================================================================
// 7. HIỂN THỊ LCD & XỬ LÝ SERIAL COMMAND
// =====================================================================
void drawPage() {
    if (!lcdAvailable) return;
    char l1[17], l2[17];
 
    if (page == 0) {
        if (isnan(tempC)) {
            snprintf(l1, sizeof(l1), "Temp  : --.-%cC", 223);
            snprintf(l2, sizeof(l2), "Humid : --.- %%");
        } else {
            snprintf(l1, sizeof(l1), "Temp  : %4.1f%cC", tempC, 223);
            snprintf(l2, sizeof(l2), "Humid : %4.1f %%", humi);
        }
    } else {
        snprintf(l1, sizeof(l1), "Air   : %4d ppm", (int)ppm);
        snprintf(l2, sizeof(l2), "[%s] %-8s", (currentMode == "AUTO" ? "A" : "M"), STATUS_TXT[airStatus]);
    }
    lcdLine(0, l1);
    lcdLine(1, l2);
}
 
void handleSerial() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == 'c' || c == 'C') {
            lcdLine(0, "Calibrating R0..");
            lcdLine(1, "Keep air clean");
            r0 = calibrateR0();
            Serial.printf("[MQ135] New R0 = %.2f kOhm -> Active\n", r0);
        }
        else if (c == 'f' || c == 'F') {
            currentMode = "MANUAL";
            fanWrite(FAN_BAD);
            Serial.println("[FAN] START (manual, 100%)");
            sendActuatorState("SERIAL_MANUAL_ON");
        }
        else if (c == 'o' || c == 'O') {
            currentMode = "MANUAL";
            fanWrite(FAN_GOOD);
            Serial.println("[FAN] STOP (manual, 0%)");
            sendActuatorState("SERIAL_MANUAL_OFF");
        }
        else if (c == 'a' || c == 'A') {
            currentMode = "AUTO";
            updateStatus();
            Serial.println("[FAN] Back to AUTO mode");
            sendActuatorState("SERIAL_AUTO");
        }
        else if (c == 't' || c == 'T') {
            runFanNoiseTest();
        }
    }
}
 
// =====================================================================
// 8. MQTT TELEMETRY & XỬ LÝ LỆNH
// =====================================================================
void sendActuatorState(const char* reason) {
    if (!mqttClient.connected()) return;
    StaticJsonDocument<256> doc;
    doc["device_id"] = DEVICE_ID;
    doc["fan_state"] = fanState;
    doc["fan_duty"]  = fanDuty;
    doc["mode"]      = currentMode;
    doc["reason"]    = reason;
 
    char jsonBuffer[256];
    serializeJson(doc, jsonBuffer);
    mqttClient.publish(TOPIC_ACTUATOR_STATE, jsonBuffer, false);
    lastReportedDuty = fanDuty;
}
 
void sendConfigState() {
    if (!mqttClient.connected()) return;
    StaticJsonDocument<256> doc;
    doc["device_id"]           = DEVICE_ID;
    doc["pollution_threshold"] = pollutionThreshold;
    doc["temp_threshold"]      = tempThreshold;
    doc["dwell_time_seconds"]  = dwellTimeSeconds;
 
    char jsonBuffer[256];
    serializeJson(doc, jsonBuffer);
    mqttClient.publish(TOPIC_CONFIG_STATE, jsonBuffer, true);
}
 
void sendTelemetry() {
    if (!mqttClient.connected()) {
        Serial.println("[MQTT WARNING] Mat ket noi MQTT, bo qua luot gui Telemetry!");
        return;
    }
 
    StaticJsonDocument<384> doc;
    doc["device_id"]   = DEVICE_ID;
    doc["seq"]         = msgSeq++;
    doc["temperature"] = isnan(tempC) ? 0.0 : tempC;
    doc["humidity"]    = isnan(humi) ? 0.0 : humi;
    doc["gas_ppm"]     = ppm;
    doc["fan_state"]   = fanState;
    doc["fan_duty"]    = fanDuty;
    doc["mode"]        = currentMode;
 
    // [SỬA] Chỉ gửi timestamp khi NTP đã đồng bộ (tránh gửi mốc năm 1970)
    time_t nowSec = time(nullptr);
    if (nowSec > 1700000000) {
        doc["timestamp"] = (uint64_t)nowSec * 1000ULL;
    }
 
    char jsonBuffer[384];
    serializeJson(doc, jsonBuffer);
 
    bool ok = mqttClient.publish(TOPIC_TELEMETRY, jsonBuffer, false);
    if (ok) {
        Serial.printf("[MQTT SUCCESS] Da gui Telemetry toi topic '%s'\n", TOPIC_TELEMETRY);
    } else {
        Serial.printf("[MQTT ERROR] Gui Telemetry toi topic '%s' THAT BAI!\n", TOPIC_TELEMETRY);
    }
}
 
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, payload, length)) return;
 
    String topicStr = String(topic);
    if (topicStr == TOPIC_ACTUATOR_SET) {
        int newState = -1;
        if (doc.containsKey("state")) {
            newState = doc["state"].is<bool>() ? (doc["state"].as<bool>() ? 1 : 0) : doc["state"].as<int>();
        } else if (doc.containsKey("fan_state")) {
            newState = doc["fan_state"].is<bool>() ? (doc["fan_state"].as<bool>() ? 1 : 0) : doc["fan_state"].as<int>();
        }
 
        if (newState != -1) {
            currentMode = "MANUAL";
            fanWrite(newState ? FAN_BAD : FAN_GOOD);
            sendActuatorState("MANUAL_COMMAND");
        }
    }
    else if (topicStr == TOPIC_MODE_SET) {
        if (doc.containsKey("mode")) {
            // [SỬA] Chỉ chấp nhận AUTO / MANUAL, tránh chuỗi lạ làm kẹt quạt
            String m = doc["mode"].as<String>();
            m.toUpperCase();
            if (m == "AUTO" || m == "MANUAL") {
                currentMode = m;
                updateStatus();
                sendActuatorState("MODE_CHANGED");
            }
        }
    }
    else if (topicStr == TOPIC_CONFIG_SET) {
        if (doc.containsKey("pollution_threshold")) pollutionThreshold = doc["pollution_threshold"];
        if (doc.containsKey("temp_threshold"))      tempThreshold      = doc["temp_threshold"];
        if (doc.containsKey("dwell_time_seconds"))  dwellTimeSeconds   = doc["dwell_time_seconds"];
        updateStatus();
        sendConfigState();
    }
}
 
void tryConnectMQTT() {
    if (mqttClient.connected()) return;
 
    unsigned long now = millis();
    if (now - lastMqttRetryTime > 5000) {
        lastMqttRetryTime = now;
 
        StaticJsonDocument<128> lwtDoc;
        lwtDoc["device_id"] = DEVICE_ID;
        lwtDoc["status"]    = "OFFLINE";
        char lwtBuffer[128];
        serializeJson(lwtDoc, lwtBuffer);
 
        if (mqttClient.connect(DEVICE_ID, MQTT_USER, MQTT_PASS, TOPIC_STATUS, 1, true, lwtBuffer)) {
            Serial.println("[MQTT] Ket noi broker THANH CONG!");
            StaticJsonDocument<128> onlineDoc;
            onlineDoc["device_id"] = DEVICE_ID;
            onlineDoc["status"]    = "ONLINE";
            char onlineBuffer[128];
            serializeJson(onlineDoc, onlineBuffer);
            mqttClient.publish(TOPIC_STATUS, onlineBuffer, true);
 
            mqttClient.subscribe(TOPIC_ACTUATOR_SET, 1);
            mqttClient.subscribe(TOPIC_MODE_SET, 1);
            mqttClient.subscribe(TOPIC_CONFIG_SET, 1);
            sendConfigState();
            sendActuatorState("RECONNECTED");
        } else {
            Serial.printf("[MQTT] Ket noi THAT BAI, state=%d\n", mqttClient.state());
        }
    }
}
 
// =====================================================================
// 9. SETUP & LOOP
// =====================================================================
void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.println("\n=== ESP32 Kitchen Monitor Firmware (Optimized) ===");
    Serial.println("Serial: c=calibrate R0 | f=START fan | o=STOP fan | a=AUTO | t=test nhieu quat");
 
    pinMode(PIN_MQ_DO, INPUT_PULLUP);
 
    fanBegin();
    fanWrite(0);
 
    Wire.begin(PIN_SDA, PIN_SCL);
    Wire.setTimeOut(100);
 
    ahtOk = aht.begin(&Wire);
    Serial.println(ahtOk ? "[AHT20] OK" : "[AHT20] NOT FOUND (0x38) - check wiring");
 
    Wire.beginTransmission(0x27);
    if (Wire.endTransmission() == 0) {
        lcd = &lcd27;
        lcdAvailable = true;
    } else {
        Wire.beginTransmission(0x3F);
        if (Wire.endTransmission() == 0) {
            lcd = &lcd3F;
            lcdAvailable = true;
        }
    }
    Serial.println(lcdAvailable ? "[LCD] OK" : "[LCD] NOT FOUND - chay khong man hinh");
 
    if (lcdAvailable) {
        lcd->init();
        lcd->backlight();
        lcdLine(0, "Kitchen Monitor");
        lcdLine(1, "Self Testing...");
    }
 
    // Self-test quạt
    fanWrite(FAN_BAD);
    delay(1000);
    fanWrite(0);
 
    // Kết nối WiFi
    lcdLine(1, "Connecting WiFi");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    int wifiTimeout = 0;
    while (WiFi.status() != WL_CONNECTED && wifiTimeout < 20) {
        delay(500);
        wifiTimeout++;
    }
 
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("[WiFi] Connected!");
        configTime(0, 0, "pool.ntp.org", "time.nist.gov");
        ntpConfigured = true;
    } else {
        Serial.println("[WiFi] NOT connected - se thu lai trong loop()");
    }
 
    // Warm-up MQ135
    for (int s = WARMUP_S; s > 0; s--) {
        readAHT();
        char l1[17], l2[17];
        snprintf(l1, sizeof(l1), "MQ WarmUp: %2ds", s);
        if (isnan(tempC)) snprintf(l2, sizeof(l2), "AHT20 Wait...");
        else              snprintf(l2, sizeof(l2), "T:%.1f%cC H:%.0f%%", tempC, 223, humi);
        lcdLine(0, l1);
        lcdLine(1, l2);
        delay(1000);
    }
 
    // Calibrate R0 (quạt đang TẮT)
    lcdLine(0, "Calibrating R0..");
    lcdLine(1, "Keep air clean");
    r0 = calibrateR0();
    Serial.printf("[MQ135] Calibrated R0 = %.2f kOhm\n", r0);
 
    mqttClient.setServer(MQTT_HOST, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);
 
    lastPage   = millis();
    lastSensor = millis() - SENSOR_MS;
}
 
void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        uint32_t nowW = millis();
        if (nowW - lastWifiRetryTime > 10000) {
            lastWifiRetryTime = nowW;
            Serial.println("[WiFi] Mat ket noi - dang thu ket noi lai...");
            WiFi.disconnect();
            WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
        }
    } else {
        if (!ntpConfigured) {
            configTime(0, 0, "pool.ntp.org", "time.nist.gov");
            ntpConfigured = true;
        }
 
        tryConnectMQTT();
        if (mqttClient.connected()) {
            mqttClient.loop();
        }
    }
 
    uint32_t now = millis();
 
    // 1. Đọc cảm biến & cập nhật logic mỗi 1 giây
    if (now - lastSensor >= SENSOR_MS) {
        lastSensor = now;
        readAHT();
        readAir();
        updateStatus();
        drawPage();
        printSerial();
    }
 
    // 2. Chuyển trang LCD mỗi 2 giây
    if (now - lastPage >= PAGE_MS) {
        lastPage = now;
        page ^= 1;
        drawPage();
    }
 
    // 3. Gửi telemetry mỗi 2 giây
    if (now - lastTelemetryTime >= 2000) {
        lastTelemetryTime = now;
        sendTelemetry();
    }
 
    // 4. Xử lý lệnh Serial
    handleSerial();
}
 