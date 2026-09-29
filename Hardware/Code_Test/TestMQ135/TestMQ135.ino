#define MQ135_AO 34
#define MQ135_DO 27

void setup() {
  Serial.begin(115200);

  pinMode(MQ135_DO, INPUT);
}

void loop() {
  int analogValue = analogRead(MQ135_AO);
  int digitalValue = digitalRead(MQ135_DO);

  Serial.print("AO: ");
  Serial.print(analogValue);

  Serial.print(" | DO: ");
  Serial.println(digitalValue);

  delay(500);
}