
#include <Arduino.h>
#include <WiFi.h>
#include <AsyncMqttClient.h>
#include <ArduinoJson.h>

extern "C" {
  #include "freertos/FreeRTOS.h"
  #include "freertos/timers.h"
}

// ================= CẤU HÌNH MẠNG & MQTT =================
#define WIFI_SSID "Galaxy A12 1635"
#define WIFI_PASSWORD "legy2788"

#define MQTT_HOST "iot-kitchen-hcmute.duckdns.org"
#define MQTT_PORT 1883
#define MQTT_USER "esp32_device"
#define MQTT_PASSWORD "Esp32S3Pass"
#define MQTT_ID "esp32_01"

const char* TOPIC_TELEMETRY      = "telemetry/esp32_01";
const char* TOPIC_STATUS         = "status/esp32_01";
const char* TOPIC_ACTUATOR_SET   = "actuator/set/esp32_01";   
const char* TOPIC_ACTUATOR_STATE = "actuator/state/esp32_01"; 
const char* TOPIC_MODE_SET       = "mode/set/esp32_01";       
const char* TOPIC_CONFIG_SET     = "config/set/esp32_01";     

// ================= CẤU HÌNH PHẦN CỨNG & FSM =================
#define FAN_PIN 23 
#define BUTTON_PIN 0 // Sử dụng nút BOOT trên mạch ESP32 làm nút bấm thủ công

enum FanState { IDLE, VENTILATING };
FanState currentState = IDLE;
bool isAutoMode = true; 

unsigned long lastSwitchTime = 0;
const unsigned long DWELL_TIME = 30000; 

unsigned long lastSensorReadTime = 0;
const unsigned long sensorInterval = 2000; 

unsigned long lastPublishTime = 0;
const unsigned long publishInterval = 5000; 

AsyncMqttClient mqttClient;
TimerHandle_t mqttReconnectTimer;
TimerHandle_t wifiReconnectTimer;
int currentBackoffDelay = 2000; 

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

  mqttClient.publish(TOPIC_STATUS, 1, true, "ONLINE");
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

// ================= XỬ LÝ LỆNH TỪ BACKEND (WEB/APP) =================
void onMqttMessage(char* topic, char* payload, AsyncMqttClientMessageProperties properties, size_t len, size_t index, size_t total) {
  String message((char*)payload, len);
  Serial.printf("[MQTT RX] Topic: %s | Message: %s\n", topic, message.c_str());
  String currentTopic = String(topic);

  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, message);
  if (error) {
    Serial.println("[LỖI] Parse JSON thất bại!");
    return;
  }

  if (currentTopic == TOPIC_MODE_SET) {
    if (!doc["mode"].isNull()) {
      isAutoMode = (doc["mode"] == "AUTO");
      Serial.printf("[SYSTEM] Chuyển sang chế độ: %s\n", isAutoMode ? "AUTO" : "MANUAL");
    }
  }
  else if (currentTopic == TOPIC_ACTUATOR_SET) {
    if (!isAutoMode && !doc["fan"].isNull()) {
      String fanCmd = doc["fan"];
      if (fanCmd == "ON" && currentState != VENTILATING) {
        digitalWrite(FAN_PIN, HIGH);
        currentState = VENTILATING;
        lastSwitchTime = millis();
        Serial.println("[WEB_MANUAL] Quạt BẬT.");
        mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"ON\"}");
      } 
      else if (fanCmd == "OFF" && currentState != IDLE) {
        digitalWrite(FAN_PIN, LOW);
        currentState = IDLE;
        lastSwitchTime = millis();
        Serial.println("[WEB_MANUAL] Quạt TẮT.");
        mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"OFF\"}");
      }
    } else if (isAutoMode) {
      Serial.println("[CẢNH BÁO] Đang ở chế độ AUTO, từ chối lệnh điều khiển tay từ Web!");
    }
  }
}

// ================= NÚT BẤM VẬT LÝ (HARDWARE MANUAL) =================
void handlePhysicalButton() {
  // Đọc trạng thái nút bấm (nhấn xuống là LOW)
  if (digitalRead(BUTTON_PIN) == LOW) {
    delay(50); // Chống dội phím (Debounce)
    if (digitalRead(BUTTON_PIN) == LOW) {
      
      // 1. Ép hệ thống chuyển sang chế độ MANUAL nếu đang ở AUTO
      if (isAutoMode) {
        isAutoMode = false;
        Serial.println("[HW_MANUAL] Chuyển sang chế độ MANUAL bằng nút bấm.");
        // Báo cho Web biết đã chuyển chế độ
        if (mqttClient.connected()) mqttClient.publish(TOPIC_MODE_SET, 1, true, "{\"mode\":\"MANUAL\"}");
      }

      // 2. Đảo trạng thái quạt
      if (currentState == IDLE) {
        digitalWrite(FAN_PIN, HIGH);
        currentState = VENTILATING;
        lastSwitchTime = millis();
        Serial.println("[HW_MANUAL] Quạt BẬT bằng nút cứng.");
        if (mqttClient.connected()) mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"ON\"}");
      } else {
        digitalWrite(FAN_PIN, LOW);
        currentState = IDLE;
        lastSwitchTime = millis();
        Serial.println("[HW_MANUAL] Quạt TẮT bằng nút cứng.");
        if (mqttClient.connected()) mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"OFF\"}");
      }

      // 3. Đợi người dùng nhả nút ra mới cho vòng lặp chạy tiếp
      while(digitalRead(BUTTON_PIN) == LOW) {
        delay(10);
      }
    }
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
    doc["timestamp"] = "2026-09-18T20:45:00Z"; 
    
    String output;
    serializeJson(doc, output);
    return output;
}

// ================= BỘ NÃO FSM =================
void updateFSM(float T, float P) {
  if (!isAutoMode) return; // Nếu đang MANUAL thì bỏ qua logic tự động

  if (millis() - lastSwitchTime < DWELL_TIME) return; 

  switch (currentState) {
    case IDLE:
      if (P >= 40.0 || (P >= 28.0 && T >= 33.0)) {
        currentState = VENTILATING;
        digitalWrite(FAN_PIN, HIGH);
        lastSwitchTime = millis();
        Serial.println("[FSM AUTO] Cảnh báo! Đã BẬT quạt.");
        if (mqttClient.connected()) mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"ON\"}");
      }
      break;

    case VENTILATING:
      if (P < 25.0 && T < 31.0) {
        currentState = IDLE;
        digitalWrite(FAN_PIN, LOW);
        lastSwitchTime = millis();
        Serial.println("[FSM AUTO] An toàn. Đã TẮT quạt.");
        if (mqttClient.connected()) mqttClient.publish(TOPIC_ACTUATOR_STATE, 1, false, "{\"fan_state\":\"OFF\"}");
      }
      break;
  }
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  pinMode(FAN_PIN, OUTPUT);
  digitalWrite(FAN_PIN, LOW);
  
  // Khởi tạo nút bấm nội bộ (kéo trở pull-up)
  pinMode(BUTTON_PIN, INPUT_PULLUP);

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
  
  // Kiểm tra nút bấm liên tục
  handlePhysicalButton();

  if (millis() - lastSensorReadTime >= sensorInterval) {
    lastSensorReadTime = millis();

    // Dữ liệu giả lập
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