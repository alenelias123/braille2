import base64
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
    camera_device: str
    camera_backends: str
    frame_width: int
    frame_height: int
    camera_warmup_frames: int
    camera_read_attempts: int
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
        camera_device=os.getenv("CAMERA_DEVICE", "").strip(),
        camera_backends=os.getenv("CAMERA_BACKENDS", "V4L2,ANY").strip(),
        frame_width=int(os.getenv("FRAME_WIDTH", "1280")),
        frame_height=int(os.getenv("FRAME_HEIGHT", "720")),
        camera_warmup_frames=int(os.getenv("CAMERA_WARMUP_FRAMES", "8")),
        camera_read_attempts=int(os.getenv("CAMERA_READ_ATTEMPTS", "20")),
        save_capture=os.getenv("SAVE_CAPTURE", "true").strip().lower() == "true",
        capture_path=Path(os.getenv("CAPTURE_PATH", "captures/latest.jpg")),
        pin_mode=pin_mode,
        led_pin=int(os.getenv("LED_PIN", "16")),
        motor_pin=int(os.getenv("MOTOR_PIN", "13")),
        dot_seconds=float(os.getenv("DOT_SECONDS", "0.2")),
    )


def parse_camera_backends(raw: str) -> List[Tuple[str, int]]:
    mapping = {
        "ANY": cv2.CAP_ANY,
        "V4L2": getattr(cv2, "CAP_V4L2", None),
        "GSTREAMER": getattr(cv2, "CAP_GSTREAMER", None),
        "FFMPEG": getattr(cv2, "CAP_FFMPEG", None),
    }
    result: List[Tuple[str, int]] = []
    seen = set()
    for token in raw.split(","):
        name = token.strip().upper()
        if not name or name in seen:
            continue
        if name not in mapping:
            raise ValueError(
                f"Unknown backend {name!r}. Use comma-separated ANY,V4L2,GSTREAMER,FFMPEG."
            )
        value = mapping[name]
        if value is None:
            continue
        seen.add(name)
        result.append((name, value))
    if not result:
        result.append(("ANY", cv2.CAP_ANY))
    return result


def open_camera(config: AppConfig) -> Tuple[cv2.VideoCapture, str, str]:
    source_obj: object
    source_label: str
    if config.camera_device:
        source_obj = config.camera_device
        source_label = config.camera_device
    else:
        source_obj = config.camera_index
        source_label = f"index {config.camera_index}"

    last_error: Optional[str] = None
    for backend_name, backend_value in parse_camera_backends(config.camera_backends):
        cap = cv2.VideoCapture(source_obj, backend_value)
        if cap.isOpened():
            return cap, backend_name, source_label
        cap.release()
        last_error = f"failed to open source {source_label} with backend {backend_name}"

    raise RuntimeError(last_error or f"Could not open camera source {source_label}.")


def capture_frame(config: AppConfig) -> bytes:
    cap, backend_name, source_label = open_camera(config)

    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.frame_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.frame_height)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        # Warm-up frames improve exposure/autofocus stability.
        for _ in range(config.camera_warmup_frames):
            cap.read()
            time.sleep(0.05)

        ok = False
        frame = None
        for _ in range(config.camera_read_attempts):
            ok, frame = cap.read()
            if ok and frame is not None:
                break
            time.sleep(0.05)
        if not ok or frame is None:
            raise RuntimeError(
                "Camera frame capture failed. "
                f"Source={source_label}, backend={backend_name}. "
                "Check camera with: ls /dev/video* and v4l2-ctl --list-devices."
            )

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
        "Answer with exactly one token: YES or NO.\n"
        "Do not include explanation or punctuation.\n"
        f"Question: {config.decision_question}"
    )

    base_payload = {
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
            "maxOutputTokens": 32,
            "responseMimeType": "text/plain",
        },
    }

    last_data: dict = {}
    for max_tokens in (32, 128):
        payload = dict(base_payload)
        payload["generationConfig"] = dict(base_payload["generationConfig"])
        payload["generationConfig"]["maxOutputTokens"] = max_tokens

        data = request_gemini(config, url, payload)
        last_data = data

        text = extract_text(data)
        if text:
            return parse_yes_no(text)

        if first_finish_reason(data) != "MAX_TOKENS":
            break

    raise ValueError(
        "Gemini returned no text response. "
        f"finishReason={first_finish_reason(last_data)!r}, response={last_data}"
    )


def request_gemini(config: AppConfig, url: str, payload: dict) -> dict:
    response = requests.post(
        url,
        json=payload,
        headers={"x-goog-api-key": config.api_key},
        timeout=30,
    )
    # Fallback: some environments only accept key as query parameter.
    if response.status_code == 401:
        response = requests.post(
            url,
            params={"key": config.api_key},
            json=payload,
            timeout=30,
        )

    if not response.ok:
        body = response.text[:500]
        hint = ""
        if response.status_code == 401:
            hint = (
                " Unauthorized. Check GEMINI_API_KEY in .env, regenerate the key, "
                "and make sure no extra spaces/quotes are present."
            )
        elif response.status_code == 403:
            hint = (
                " Forbidden. API key may be restricted or Gemini API access is not enabled."
            )
        elif response.status_code == 404:
            hint = (
                f" Model not found or unavailable: {config.model}. "
                "Try a currently available model (for example gemini-2.5-flash)."
            )
        raise RuntimeError(
            f"Gemini API request failed with HTTP {response.status_code}.{hint} "
            f"Response body (truncated): {body}"
        )
    return response.json()


def first_finish_reason(api_data: dict) -> str:
    candidates = api_data.get("candidates") or []
    if not candidates:
        return ""
    return str(candidates[0].get("finishReason") or "")


def extract_text(api_data: dict) -> str:
    candidates = api_data.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if text:
            return text
    return ""


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
