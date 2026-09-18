/**
 * @file main.cpp
 * @author Le Thanh Tai
 * @brief ESP32 Async MQTT Node - FSM, Autonomous Mode, Telemetry (QoS 0) & Control (QoS 1)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <AsyncMqttClient.h>
#include <ArduinoJson.h>

extern "C" {
  #include "freertos/FreeRTOS.h"
  #include "freertos/timers.h"
}

// ================= CẤU HÌNH MẠNG & MQTT =================
#define WIFI_SSID "Thanh Nhan 5G"
#define WIFI_PASSWORD "0961498559"

// Cấu hình Broker theo Backend mới (DuckDNS + Xác thực)
#define MQTT_HOST "iot-kitchen-hcmute.duckdns.org"
#define MQTT_PORT 1883
#define MQTT_USER "esp32_device"
#define MQTT_PASSWORD "Esp32S3Pass"
#define MQTT_ID "esp32_01"

// Các topic giao tiếp đã được phân rã theo kiến trúc chuẩn
const char* TOPIC_TELEMETRY      = "telemetry/esp32_01";
const char* TOPIC_STATUS         = "status/esp32_01";
const char* TOPIC_ACTUATOR_SET   = "actuator/set/esp32_01";   // Nhận lệnh BẬT/TẮT quạt
const char* TOPIC_ACTUATOR_STATE = "actuator/state/esp32_01"; // Báo cáo trạng thái quạt
const char* TOPIC_MODE_SET       = "mode/set/esp32_01";       // Nhận lệnh đổi chế độ AUTO/MANUAL
const char* TOPIC_CONFIG_SET     = "config/set/esp32_01";     // Cài đặt cấu hình (ngưỡng nhiệt/ẩm...)
// =========================================================

// ================= CẤU HÌNH PHẦN CỨNG & FSM =================
#define FAN_PIN 23 // Chân GPIO kích MOSFET điều khiển quạt

enum FanState { IDLE, VENTILATING };
FanState currentState = IDLE;
bool isAutoMode = true; // Cờ quản lý chế độ (Auto / Manual)

unsigned long lastSwitchTime = 0;
const unsigned long DWELL_TIME = 30000; // 30 giây khóa trạng thái (Anti-chattering)

unsigned long lastSensorReadTime = 0;
const unsigned long sensorInterval = 2000; // 2 giây đọc cảm biến 1 lần

unsigned long lastPublishTime = 0;
const unsigned long publishInterval = 5000; // 5 giây publish 1 lần
// ============================================================

AsyncMqttClient mqttClient;
TimerHandle_t mqttReconnectTimer;
TimerHandle_t wifiReconnectTimer;
int currentBackoffDelay = 2000; // Bắt đầu ở 2s cho Exponential Backoff

// ================= HÀM KẾT NỐI MẠNG =================
void connectToWifi(TimerHandle_t xTimer) {
  Serial.println("[WIFI] Đang kết nối...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void connectToMqtt(TimerHandle_t xTimer) {
  Serial.println("[MQTT] Đang kết nối Broker...");
  mqttClient.connect();
}

void WiFiEvent(WiFiEvent_t event) {
  switch(event) {
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.println("[WIFI] Đã kết nối. Bắt đầu gọi MQTT...");
      xTimerStart(mqttReconnectTimer, 0); 
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      Serial.println("[WIFI] Mất kết nối mạng!");
      xTimerStop(mqttReconnectTimer, 0); 
      xTimerStart(wifiReconnectTimer, 0);
      break;
    default: break;
  }
}

void onMqttConnect(bool sessionPresent) {
  Serial.println("[MQTT] Kết nối thành công!");
  
  currentBackoffDelay = 2000; 
  xTimerChangePeriod(mqttReconnectTimer, pdMS_TO_TICKS(currentBackoffDelay), 0);

  // 1. Gửi trạng thái ONLINE (QoS 1, Retain = true)
  mqttClient.publish(TOPIC_STATUS, 1, true, "ONLINE");
  
  // 2. Lắng nghe các lệnh điều khiển đã phân rã (QoS 1)
  mqttClient.subscribe(TOPIC_ACTUATOR_SET, 1);
  mqttClient.subscribe(TOPIC_MODE_SET, 1);
  mqttClient.subscribe(TOPIC_CONFIG_SET, 1);
}

void onMqttDisconnect(AsyncMqttClientDisconnectReason reason) {
  Serial.println("[MQTT] Ngắt kết nối Broker.");
  if (WiFi.isConnected()) {
    currentBackoffDelay *= 2;
    if (currentBackoffDelay > 60000) currentBackoffDelay = 60000;
    
    Serial.printf("[MQTT] Thử lại sau %d ms\n", currentBackoffDelay);
    xTimerChangePeriod(mqttReconnectTimer, pdMS_TO_TICKS(currentBackoffDelay), 0);
  }
}

// ================= XỬ LÝ LỆNH TỪ BACKEND (OVERRIDE) =================
void onMqttMessage(char* topic, char* payload, AsyncMqttClientMessageProperties properties, size_t len, size_t index, size_t total) {
  // Khởi tạo String trực tiếp từ mảng byte - tối ưu RAM
  String message((char*)payload, len);
  
  Serial.printf("[MQTT RX] Topic: %s | Message: %s\n", topic, message.c_str());
  String currentTopic = String(topic);

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, message);
  if (error) {
    Serial.println("[LỖI] Parse JSON thất bại!");
    return;
  }

  // --- XỬ LÝ LỆNH CHUYỂN CHẾ ĐỘ ---
  if (currentTopic == TOPIC_MODE_SET) {
    if (!doc["mode"].isNull()) {
      String mode = doc["mode"];
      isAutoMode = (mode == "AUTO");
      Serial.printf("[SYSTEM] Chuyển sang chế độ: %s\n", isAutoMode ? "AUTO" : "MANUAL");
    }
  }

  // --- XỬ LÝ LỆNH ĐIỀU KHIỂN QUẠT (Chỉ nhận khi ở MANUAL) ---
  else if (currentTopic == TOPIC_ACTUATOR_SET) {
    if (!isAutoMode && !doc["fan"].isNull()) {
      String fanCmd = doc["fan"];
      if (fanCmd == "ON" && currentState != VENTILATING) {
        digitalWrite(FAN_PIN, HIGH);
        currentState = VENTILATING;
        lastSwitchTime = millis();
        Serial.println("[OVERRIDE] Quạt BẬT bằng tay.");
        mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"ON\"}");
      } 
      else if (fanCmd == "OFF" && currentState != IDLE) {
        digitalWrite(FAN_PIN, LOW);
        currentState = IDLE;
        lastSwitchTime = millis();
        Serial.println("[OVERRIDE] Quạt TẮT bằng tay.");
        mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"OFF\"}");
      }
    } else if (isAutoMode) {
      Serial.println("[CẢNH BÁO] Đang ở chế độ AUTO, từ chối lệnh điều khiển tay!");
    }
  }

  // --- XỬ LÝ LỆNH CẤU HÌNH ---
  else if (currentTopic == TOPIC_CONFIG_SET) {
    Serial.println("[CONFIG] Nhận lệnh cấu hình mới.");
  }
}

// ================= ĐÓNG GÓI JSON =================
String buildTelemetryPayload(float temp, float hum, float pol) {
    JsonDocument doc;
    doc["device_id"] = MQTT_ID;
    doc["temp"] = temp;
    doc["hum"] = hum;
    doc["pol"] = pol;
    doc["fan_state"] = (currentState == VENTILATING) ? "ON" : "OFF";
    doc["mode"] = isAutoMode ? "AUTO" : "MANUAL";
    // Tạm thời hardcode, sẽ được cập nhật khi tích hợp NTPClient hoặc Backend tự gán
    doc["timestamp"] = "2026-09-18T20:45:00Z"; 
    
    String output;
    serializeJson(doc, output);
    return output;
}

// ================= BỘ NÃO FSM (CHỐNG NHIỄU) =================
void updateFSM(float T, float P) {
  if (!isAutoMode) return; 

  if (millis() - lastSwitchTime < DWELL_TIME) {
    return; 
  }

  switch (currentState) {
    case IDLE:
      if (P >= 40.0 || (P >= 28.0 && T >= 33.0)) {
        currentState = VENTILATING;
        digitalWrite(FAN_PIN, HIGH);
        lastSwitchTime = millis();
        Serial.println("[FSM AUTO] Cảnh báo! Đã BẬT quạt.");
        
        if (mqttClient.connected()) {
            mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"ON\"}");
        }
      }
      break;

    case VENTILATING:
      if (P < 25.0 && T < 31.0) {
        currentState = IDLE;
        digitalWrite(FAN_PIN, LOW);
        lastSwitchTime = millis();
        Serial.println("[FSM AUTO] An toàn. Đã TẮT quạt.");
        
        if (mqttClient.connected()) {
            mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"OFF\"}");
        }
      }
      break;
  }
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  pinMode(FAN_PIN, OUTPUT);
  digitalWrite(FAN_PIN, LOW);

  mqttReconnectTimer = xTimerCreate("mqttTimer", pdMS_TO_TICKS(currentBackoffDelay), pdFALSE, (void*)0, connectToMqtt);
  wifiReconnectTimer = xTimerCreate("wifiTimer", pdMS_TO_TICKS(2000), pdFALSE, (void*)0, connectToWifi);

  WiFi.onEvent(WiFiEvent);
  
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCredentials(MQTT_USER, MQTT_PASSWORD); 
  mqttClient.setClientId(MQTT_ID);
  
  mqttClient.setWill(TOPIC_STATUS, 1, true, "OFFLINE");
  
  mqttClient.onConnect(onMqttConnect);
  mqttClient.onDisconnect(onMqttDisconnect);
  mqttClient.onMessage(onMqttMessage);

  xTimerStart(wifiReconnectTimer, 0);
}

// ================= VÒNG LẶP CHÍNH =================
void loop() {
  if (millis() - lastSensorReadTime >= sensorInterval) {
    lastSensorReadTime = millis();

    // Giả lập dữ liệu cảm biến trước khi ghép nối phần cứng thật
    float t = 25.0 + random(0, 100) / 10.0;
    float h = 60.0 + random(0, 100) / 10.0;
    float p = random(20, 50); 

    updateFSM(t, p);

    if (mqttClient.connected() && (millis() - lastPublishTime >= publishInterval)) {
      lastPublishTime = millis();
      
      String jsonPayload = buildTelemetryPayload(t, h, p);
      mqttClient.publish(TOPIC_TELEMETRY, 0, false, jsonPayload.c_str());
    }
  }
}