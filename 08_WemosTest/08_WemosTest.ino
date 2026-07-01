void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
  digitalWrite(LED_BUILTIN, LOW);   // D1 mini 內建 LED 低電位亮
  delay(500);
  digitalWrite(LED_BUILTIN, HIGH);
  delay(500);
}
