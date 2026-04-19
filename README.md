# Raspberry Pi 5 Environment Assistance + ASL Translation System

This project runs continuously and uses two switches:

- Switch 1 (`MODE_SWITCH_PIN`, pin 29):
  short press toggles **Environment Assistance Mode**
  long press starts **ASL Sign Translation Mode**
- Switch 2 (`SELECTOR_SWITCH_PIN`, pin 31):
  in **Environment Assistance Mode**, it selects a function by press duration
  in **ASL Sign Translation Mode**, a short press stops video recording and starts Gemini translation

Environment Assistance functions:

1. Human Presence Check: long press Switch 2 for 3+ seconds.  
   Capture one camera frame -> Gemini YES/NO -> Morse on:
   - LED pin 11
   - Motor pin 36
   After playback, the system returns to the selection menu.
2. Distance Navigation Alert: hold Switch 2 for about 2-3 seconds.  
   HC-SR04 distance check starts and stays active as a navigation mode.  
   If distance `< 150 cm`, motor pin 36 and LED pin 13 pulse.  
   Pulse frequency increases as object gets closer.  
   Press Switch 2 again to exit navigation mode.
3. Room Summary To Morse: short press Switch 2.  
   Capture a USB camera frame -> Gemini returns a minimal room summary -> summary is converted to Morse on:
   - LED pin 11
   - Motor pin 36
   The Gemini prompt is constrained to produce a short Morse-safe uppercase description such as `PERSON DOOR TABLE`.  
   After playback, the system returns to the selection menu.

ASL Sign Translation:

- Long press Switch 1 for about 2.5 seconds to start recording from the USB camera.
- Recording continues until a short press of Switch 2 is detected.
- The recorded video is sent to Gemini with a prompt tuned to return the shortest corrected uppercase English phrase possible for later Braille conversion.
- The translated English text is printed in the terminal.

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
- `ROOM_DESCRIPTION_PROMPT` (defaults to a minimal room-summary prompt for Morse output)
- `ASL_TRANSLATION_PROMPT` (defaults to a compact ASL-to-English translation prompt)
- `MODE_SWITCH_LONG_PRESS_SECONDS` if you want a different Switch 1 long-press threshold
- `SELECTOR_MEDIUM_PRESS_SECONDS` and `SELECTOR_LONG_PRESS_SECONDS` if you want different Switch 2 timing thresholds
- Camera settings (`CAMERA_DEVICE=/dev/video0` recommended for USB camera)
- Video settings (`VIDEO_FRAME_WIDTH`, `VIDEO_FRAME_HEIGHT`, `VIDEO_FPS`, `VIDEO_CAPTURE_PATH`) for ASL recording
- Pin settings if your wiring differs

The canonical pin map now lives in [pin_defaults.py](/d:/apps/braille2/pin_defaults.py:1).  
`.env` pin values are treated as overrides on top of that file, which helps the app recover from stale pin values left over from earlier wiring revisions.

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

These are the defaults in `.env.example` and `pin_defaults.py`. If your hardware is wired differently, update `.env` accordingly.

## 4. Run

```bash
python3 main.py
```

Typical runtime logs:

```text
[10:21:18] Switch 1 (pin 29) released after 0.20s -> Environment Assistance Mode ENABLED
[10:21:18] Environment Assistance Mode menu: long press -> Human Presence Check, medium press (2.0s-3.0s) -> Distance Navigation Alert, short press -> Room Summary To Morse.
[10:21:22] Switch 2 (pin 31) released after 3.40s -> Selected Human Presence Check via long press (3.0s+)
[10:21:22] Human Presence Check result: YES | Morse: -.-- . ...
[10:21:30] Switch 2 (pin 31) released after 2.40s -> Selected Distance Navigation Alert via medium press (2.0s-3.0s)
[10:21:30] Distance Navigation Alert active. Click Switch 2 to exit this mode.
[10:21:31] Distance Navigation Alert: distance=92.6 cm | threshold=150.0 cm | alert=ON
[10:21:34] Switch 2 (pin 31) released after 0.18s -> exiting Distance Navigation Alert
[10:21:38] Switch 2 (pin 31) released after 0.30s -> Selected Room Summary To Morse via short press (<2.0s)
[10:21:39] Room summary: PERSON DOOR TABLE | Morse: .--. . .-. ... --- -. / -.. --- --- .-. / - .- -... .-.. .
[10:21:50] Switch 1 (pin 29) released after 2.70s -> ASL Sign Translation Mode
[10:21:50] ASL Sign Translation active. Recording video now. Short press Switch 2 to stop recording and start translation.
[10:21:56] Switch 2 (pin 31) released after 0.20s -> stopping ASL video capture
[10:21:59] ASL translation: OPEN DOOR
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
