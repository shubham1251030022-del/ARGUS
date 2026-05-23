/*
  ARGUS — Hardware: arduino_sketch.ino  [v4 — Single LED Mode]
  Member 2: Shubham Pitty | VIT Pune CSAIML-E Group 01

  Components:
    Green LED  → Pin 6 (220Ω) — Normal exam running
    Amber LED  → Pin 5 (220Ω) — Warning zone
    Red LED    → Pin 7 (220Ω) — Malpractice confirmed
    Buzzer     → Pin 8        — Audio alert
    LCD 16x2   → SDA=A4, SCL=A5

  LED behavior — only ONE LED on at a time:
    Exam start     → GREEN only
    Warning        → GREEN off → AMBER on
    Warning clears → AMBER off → GREEN on
    Alert          → AMBER off → GREEN off → RED on
    Alert clears   → RED off → GREEN on
    Exam stop      → ALL off

  Serial Commands (9600 baud):
    PING, EXAM_START, EXAM_STOP
    WARNING:<bench>:<name>
    ALERT:<bench>:<name>
    CLEAR, WARN_CLEAR
*/

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

#define PIN_GREEN   6
#define PIN_AMBER   5
#define PIN_RED     7
#define PIN_BUZZER  8

LiquidCrystal_I2C lcd(0x27, 16, 2);

String  inputBuffer = "";
bool    examRunning = false;
bool    alertActive = false;
bool    warnActive  = false;
unsigned long alertTime = 0;
unsigned long warnTime  = 0;

byte alertIcon[8] = {
  0b00100, 0b01110, 0b01110, 0b11111,
  0b11111, 0b00000, 0b00100, 0b00000
};
byte warnIcon[8] = {
  0b00100, 0b01110, 0b01010, 0b10001,
  0b11111, 0b00100, 0b00000, 0b00100
};

// ── Core helper: set exactly one LED, all others off ─────────────────────────
void setLed(int pin) {
  digitalWrite(PIN_GREEN, LOW);
  digitalWrite(PIN_AMBER, LOW);
  digitalWrite(PIN_RED,   LOW);
  delay(30);  // brief gap so previous LED fully off
  if (pin > 0) digitalWrite(pin, HIGH);
}

void allOff() {
  digitalWrite(PIN_GREEN,  LOW);
  digitalWrite(PIN_AMBER,  LOW);
  digitalWrite(PIN_RED,    LOW);
  digitalWrite(PIN_BUZZER, LOW);
}

void beep(int times, int onMs, int offMs) {
  for (int i = 0; i < times; i++) {
    digitalWrite(PIN_BUZZER, HIGH); delay(onMs);
    digitalWrite(PIN_BUZZER, LOW);
    if (i < times - 1) delay(offMs);
  }
}

void lcdLine(int row, String text) {
  while (text.length() < 16) text += " ";
  lcd.setCursor(0, row);
  lcd.print(text.substring(0, 16));
}

void showMonitoring() {
  lcd.clear();
  lcdLine(0, "EXAM IN PROGRESS");
  lcdLine(1, "  MONITORING... ");
}

void setup() {
  Serial.begin(9600);
  pinMode(PIN_GREEN,  OUTPUT);
  pinMode(PIN_AMBER,  OUTPUT);
  pinMode(PIN_RED,    OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  allOff();

  lcd.init();
  lcd.backlight();
  lcd.createChar(0, alertIcon);
  lcd.createChar(1, warnIcon);

  // Boot sweep — one at a time
  lcd.clear();
  lcdLine(0, "  ARGUS  SYSTEM ");
  lcdLine(1, "  INITIALIZING..");

  setLed(PIN_GREEN); delay(300);
  setLed(PIN_AMBER); delay(300);
  setLed(PIN_RED);   delay(300);
  beep(1, 100, 0);
  allOff();
  delay(200);

  lcd.clear();
  lcdLine(0, "  ARGUS  READY  ");
  lcdLine(1, " VIT PUNE G-01  ");

  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      processCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      inputBuffer += c;
    }
  }

  // Auto-clear alert after 30s → back to green
  if (alertActive && millis() - alertTime > 30000) {
    alertActive = false;
    if (examRunning) {
      setLed(PIN_GREEN);
      showMonitoring();
    } else {
      allOff();
    }
  }

  // Auto-clear warning after 15s → back to green
  if (warnActive && !alertActive && millis() - warnTime > 15000) {
    warnActive = false;
    if (examRunning) {
      setLed(PIN_GREEN);
      showMonitoring();
    } else {
      allOff();
    }
  }
}

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  // PING
  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  // EXAM_START → Green only
  if (cmd == "EXAM_START") {
    examRunning = true;
    alertActive = false;
    warnActive  = false;
    setLed(PIN_GREEN);
    showMonitoring();
    beep(2, 80, 80);
    Serial.println("OK_EXAM_START");
    return;
  }

  // EXAM_STOP → All off
  if (cmd == "EXAM_STOP") {
    examRunning = false;
    alertActive = false;
    warnActive  = false;
    allOff();
    lcd.clear();
    lcdLine(0, "  EXAM  ENDED   ");
    lcdLine(1, " ARGUS STANDBY  ");
    beep(1, 400, 0);
    Serial.println("OK_EXAM_STOP");
    return;
  }

  // CLEAR → back to green if exam running
  if (cmd == "CLEAR") {
    alertActive = false;
    warnActive  = false;
    if (examRunning) {
      setLed(PIN_GREEN);
      showMonitoring();
    } else {
      allOff();
      lcd.clear();
      lcdLine(0, "  ARGUS  READY  ");
      lcdLine(1, " VIT PUNE G-01  ");
    }
    Serial.println("OK_CLEAR");
    return;
  }

  // WARN_CLEAR → amber off, back to green
  if (cmd == "WARN_CLEAR") {
    warnActive = false;
    if (!alertActive && examRunning) {
      setLed(PIN_GREEN);
      showMonitoring();
    }
    Serial.println("OK_WARN_CLEAR");
    return;
  }

  // WARNING → Green off, Amber on (skip if alert active)
  if (cmd.startsWith("WARNING:")) {
    if (alertActive) {
      Serial.println("OK_WARNING_SKIPPED");
      return;
    }
    String payload = cmd.substring(8);
    int sep      = payload.indexOf(':');
    String bench = sep >= 0 ? payload.substring(0, sep) : payload;
    String name  = sep >= 0 ? payload.substring(sep + 1) : "UNKNOWN";
    if (name.length() > 10) name = name.substring(0, 10);

    warnActive = true;
    warnTime   = millis();

    // Green off → Amber on (via setLed — guarantees single LED)
    setLed(PIN_AMBER);

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.write(byte(1));
    lcd.print(" WARNING ");
    lcd.print(bench);
    lcdLine(1, name);

    beep(1, 120, 0);
    Serial.println("OK_WARNING");
    return;
  }

  // ALERT → All off → Red on only
  if (cmd.startsWith("ALERT:")) {
    String payload = cmd.substring(6);
    int sep      = payload.indexOf(':');
    String bench = sep >= 0 ? payload.substring(0, sep) : payload;
    String name  = sep >= 0 ? payload.substring(sep + 1) : "UNKNOWN";
    if (name.length() > 10) name = name.substring(0, 10);

    alertActive = true;
    alertTime   = millis();
    warnActive  = false;

    // setLed turns all others off first, then red on
    setLed(PIN_RED);

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.write(byte(0));
    lcd.print(" MALPRACTICE!  ");
    lcdLine(1, bench + ": " + name);

    beep(3, 250, 150);
    Serial.println("OK_ALERT");
    return;
  }

  Serial.println("UNKNOWN:" + cmd);
}
