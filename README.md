# Raspberry Pi 5 Vision + Distance Selector System

This project runs continuously and uses two switches:

- Switch 1 (`MODE_SWITCH_PIN`, pin 29): toggles **Mode 1** ON/OFF
- Switch 2 (`SELECTOR_SWITCH_PIN`, pin 31): cycles selector functions when Mode 1 is ON

Selector functions:

1. Vision function (existing behavior): capture camera frame -> Gemini YES/NO -> Morse on:
   - LED pin 11
   - Motor pin 36
2. Distance function (new behavior): HC-SR04 distance check.  
   If distance `< 150 cm`, motor pin 36 and LED pin 13 pulse.  
   Pulse frequency increases as object gets closer.

The program prints switch press events and live status updates in terminal.

## 1. Install dependencies on Raspberry Pi

```bash
sudo apt update
sudo apt install -y python3-opencv python3-pip python3-rpi-lgpio python3-lgpio v4l-utils
python3 -m pip install -r requirements.txt
```

If legacy `RPi.GPIO` is installed, remove it:

```bash
sudo apt remove -y python3-rpi.gpio
```

Verify GPIO import:

```bash
python3 -c "import RPi.GPIO as GPIO; print(GPIO.VERSION)"
```

## 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

- `GEMINI_API_KEY` (required)
- `DECISION_QUESTION` (defaults to human presence question)
- Camera settings (`CAMERA_DEVICE=/dev/video0` recommended for USB camera)
- Pin settings if your wiring differs

## 3. Pin mapping

With `PIN_MODE=BOARD`:

- LED 1: `LED_PIN=11`
- LED 2: `AUX_LED_PIN=13`
- Motor: `MOTOR_PIN=36`
- Switch 1: `MODE_SWITCH_PIN=29`
- Switch 2: `SELECTOR_SWITCH_PIN=31`
- HC-SR04 trigger: `HCSR04_TRIGGER_PIN=16`
- HC-SR04 echo: `HCSR04_ECHO_PIN=18`
- HC-SR04 VCC: physical pin `2` (5V)

These are the defaults in `.env.example`. If your hardware is wired differently, update `.env` accordingly.

## 4. Run

```bash
python3 main.py
```

Typical runtime logs:

```text
[10:21:18] Switch 1 (pin 29) pressed -> Mode 1 ENABLED
[10:21:22] Switch 2 (pin 31) pressed -> Selected Function 1: Vision Human Check
[10:21:22] Vision result: YES | Morse: -.-- . ...
[10:21:30] Switch 2 (pin 31) pressed -> Selected Function 2: HC-SR04 Distance Alert
[10:21:31] Function 2: distance=92.6 cm | threshold=150.0 cm | alert=ON
```

## 5. API key check

```bash
GEMINI_API_KEY="$(sed -n 's/^GEMINI_API_KEY=//p' .env | tr -d '\r\n')"
GEMINI_MODEL="$(sed -n 's/^GEMINI_MODEL=//p' .env | tr -d '\r\n')"

curl -sS "https://generativelanguage.googleapis.com/v1beta/models/${GEMINI_MODEL}:generateContent" \
  -H "x-goog-api-key: ${GEMINI_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Reply YES"}]}]}'
```

## 6. Camera troubleshooting

```bash
ls /dev/video*
v4l2-ctl --list-devices
```

Then set `.env`:

```env
CAMERA_DEVICE=/dev/video0
CAMERA_BACKENDS=V4L2,ANY
```
