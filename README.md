# Raspberry Pi 5 USB Camera -> Gemini YES/NO -> Morse on Pins 16 and 13

This project captures one frame from a USB camera, sends it to Gemini for binary analysis (`YES`/`NO`), converts the result to Morse code, and outputs Morse timing on:

- Pin 16: LED
- Pin 13: Motor

By default, these are **physical BOARD pin numbers**.

## 1. Install dependencies on Raspberry Pi

```bash
sudo apt update
sudo apt install -y python3-opencv python3-pip
python3 -m pip install -r requirements.txt
```

## 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

- Set `GEMINI_API_KEY`
- Set `DECISION_QUESTION` (this defines what YES/NO means)
- Keep `PIN_MODE=BOARD` for physical pins, or set `PIN_MODE=BCM` for GPIO numbering

## 3. Run

```bash
python3 main.py
```

Expected terminal output:

```text
Decision: YES
Morse: -.-- . ...
```

`YES` becomes `-.-- . ...` and `NO` becomes `-. ---`.

## Notes

- The script forces Gemini output to one word (`YES` or `NO`) and errors out if ambiguous.
- GPIO is always cleaned up on exit, including failure cases.
- If camera capture is enabled (`SAVE_CAPTURE=true`), the frame is saved to `captures/latest.jpg`.
