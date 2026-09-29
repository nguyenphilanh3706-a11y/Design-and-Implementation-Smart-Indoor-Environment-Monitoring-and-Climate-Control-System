#include <Wire.h>
#include <Adafruit_AHTX0.h>

Adafruit_AHTX0 aht;

void setup() {
  Serial.begin(115200);

  Wire.begin(21, 22);

  if (!aht.begin()) {
    Serial.println("Could not find AHT20!");
    while (1) delay(10);
  }

  Serial.println("AHT20 found!");
}

void loop() {
  sensors_event_t humidity, temp;

  aht.getEvent(&humidity, &temp);

  Serial.print("Temperature: ");
  Serial.print(temp.temperature);
  Serial.println(" °C");

  Serial.print("Humidity: ");
  Serial.print(humidity.relative_humidity);
  Serial.println(" %");

  Serial.println("----------------");

  delay(2000);
}