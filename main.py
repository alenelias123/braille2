import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import cv2
import requests
from dotenv import load_dotenv

try:
    import RPi.GPIO as GPIO
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "RPi.GPIO module not found. On Raspberry Pi 5 install: pip install rpi-lgpio"
    ) from exc


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

MORSE_MAP: Dict[str, str] = {
    "A": ".-",
    "B": "-...",
    "C": "-.-.",
    "D": "-..",
    "E": ".",
    "F": "..-.",
    "G": "--.",
    "H": "....",
    "I": "..",
    "J": ".---",
    "K": "-.-",
    "L": ".-..",
    "M": "--",
    "N": "-.",
    "O": "---",
    "P": ".--.",
    "Q": "--.-",
    "R": ".-.",
    "S": "...",
    "T": "-",
    "U": "..-",
    "V": "...-",
    "W": ".--",
    "X": "-..-",
    "Y": "-.--",
    "Z": "--..",
    "0": "-----",
    "1": ".----",
    "2": "..---",
    "3": "...--",
    "4": "....-",
    "5": ".....",
    "6": "-....",
    "7": "--...",
    "8": "---..",
    "9": "----.",
}


@dataclass
class AppConfig:
    api_key: str
    model: str
    decision_question: str
    camera_index: int
    frame_width: int
    frame_height: int
    save_capture: bool
    capture_path: Path
    pin_mode: str
    led_pin: int
    motor_pin: int
    dot_seconds: float


def load_config() -> AppConfig:
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required. Put it in .env.")

    decision_question = os.getenv("DECISION_QUESTION", "").strip()
    if not decision_question:
        raise ValueError("DECISION_QUESTION is required. Define what YES/NO means.")

    pin_mode = os.getenv("PIN_MODE", "BOARD").strip().upper()
    if pin_mode not in {"BOARD", "BCM"}:
        raise ValueError("PIN_MODE must be BOARD or BCM.")

    return AppConfig(
        api_key=api_key,
        model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview").strip(),
        decision_question=decision_question,
        camera_index=int(os.getenv("CAMERA_INDEX", "0")),
        frame_width=int(os.getenv("FRAME_WIDTH", "1280")),
        frame_height=int(os.getenv("FRAME_HEIGHT", "720")),
        save_capture=os.getenv("SAVE_CAPTURE", "true").strip().lower() == "true",
        capture_path=Path(os.getenv("CAPTURE_PATH", "captures/latest.jpg")),
        pin_mode=pin_mode,
        led_pin=int(os.getenv("LED_PIN", "16")),
        motor_pin=int(os.getenv("MOTOR_PIN", "13")),
        dot_seconds=float(os.getenv("DOT_SECONDS", "0.2")),
    )


def capture_frame(config: AppConfig) -> bytes:
    cap = cv2.VideoCapture(config.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {config.camera_index}.")

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.frame_height)

        # Warm-up frames improve exposure/autofocus stability.
        for _ in range(5):
            cap.read()
            time.sleep(0.05)

        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("Camera frame capture failed.")

        ok, jpg = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG.")

        jpg_bytes = jpg.tobytes()
        if config.save_capture:
            config.capture_path.parent.mkdir(parents=True, exist_ok=True)
            config.capture_path.write_bytes(jpg_bytes)

        return jpg_bytes
    finally:
        cap.release()


def ask_gemini_yes_no(config: AppConfig, image_bytes: bytes) -> str:
    url = f"{GEMINI_API_BASE}/{config.model}:generateContent"
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You must answer with exactly one word: YES or NO.\n"
        f"Question: {config.decision_question}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": image_b64,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 4,
        },
    }

    response = requests.post(
        url,
        json=payload,
        headers={"x-goog-api-key": config.api_key},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    text = extract_text(data)
    decision = parse_yes_no(text)
    return decision


def extract_text(api_data: dict) -> str:
    try:
        candidates = api_data["candidates"]
        if not candidates:
            raise ValueError("No candidates in response.")
        parts = candidates[0]["content"]["parts"]
        if not parts:
            raise ValueError("No text parts in response.")
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise ValueError("Empty text in model response.")
        return text
    except Exception as exc:
        raise ValueError(f"Unexpected Gemini response format: {api_data}") from exc


def parse_yes_no(text: str) -> str:
    normalized = text.strip().upper()
    if normalized in {"YES", "NO"}:
        return normalized

    matches = re.findall(r"\b(YES|NO)\b", normalized)
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    raise ValueError(f"Model did not return a clear YES/NO. Raw response: {text!r}")


def text_to_morse(text: str) -> str:
    letters = []
    for char in text.upper():
        if char == " ":
            letters.append("/")
            continue
        code = MORSE_MAP.get(char)
        if not code:
            raise ValueError(f"Character {char!r} has no Morse mapping.")
        letters.append(code)
    return " ".join(letters)


def setup_gpio(config: AppConfig) -> None:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD if config.pin_mode == "BOARD" else GPIO.BCM)
    GPIO.setup(config.led_pin, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(config.motor_pin, GPIO.OUT, initial=GPIO.LOW)


def set_outputs(config: AppConfig, state: bool) -> None:
    value = GPIO.HIGH if state else GPIO.LOW
    GPIO.output(config.led_pin, value)
    GPIO.output(config.motor_pin, value)


def signal_morse(config: AppConfig, morse: str) -> None:
    unit = config.dot_seconds
    words = morse.split(" / ")
    for word_index, word in enumerate(words):
        letters = word.split(" ")
        for letter_index, letter in enumerate(letters):
            for symbol_index, symbol in enumerate(letter):
                set_outputs(config, True)
                time.sleep(unit if symbol == "." else 3 * unit)
                set_outputs(config, False)
                if symbol_index < len(letter) - 1:
                    time.sleep(unit)

            if letter_index < len(letters) - 1:
                time.sleep(3 * unit)

        if word_index < len(words) - 1:
            time.sleep(7 * unit)


def main() -> None:
    config = load_config()
    setup_gpio(config)
    try:
        image = capture_frame(config)
        decision = ask_gemini_yes_no(config, image)
        morse = text_to_morse(decision)

        print(f"Decision: {decision}")
        print(f"Morse: {morse}")
        signal_morse(config, morse)
    finally:
        set_outputs(config, False)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
