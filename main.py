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
from pin_defaults import (
    DEFAULT_PIN_LAYOUTS,
    HEADER_BCM_PINS,
    LEGACY_PIN_CANDIDATES,
    NON_GPIO_BOARD_PINS,
    PIN_FIELDS,
)

try:
    import RPi.GPIO as GPIO
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "RPi.GPIO module not found. On Raspberry Pi 5 install: python3-rpi-lgpio"
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
    room_description_prompt: str
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
    aux_led_pin: int
    mode_switch_pin: int
    selector_switch_pin: int
    hcsr04_trigger_pin: int
    hcsr04_echo_pin: int
    switch_active_low: bool
    switch_debounce_seconds: float
    selector_medium_press_seconds: float
    selector_long_press_seconds: float
    dot_seconds: float
    repeat_same_morse: bool
    hcsr04_timeout_seconds: float
    distance_threshold_cm: float
    distance_min_period_seconds: float
    distance_max_period_seconds: float
    distance_read_interval_seconds: float
    distance_log_interval_seconds: float
    main_loop_sleep_seconds: float


@dataclass
class ButtonState:
    prev_pressed: bool = False
    last_event_at: float = 0.0
    press_started_at: float = 0.0


@dataclass
class ButtonEvent:
    pressed: bool = False
    released: bool = False
    press_duration: float = 0.0


@dataclass
class PulseState:
    is_on: bool = False
    next_toggle_at: float = 0.0


def log(message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from exc


def load_pin_values(pin_mode: str) -> Dict[str, int]:
    defaults = DEFAULT_PIN_LAYOUTS[pin_mode]
    pin_values = {name: env_int(name, defaults[name]) for name in PIN_FIELDS}

    for message in repair_legacy_pin_issues(pin_mode, pin_values):
        log(message)

    return pin_values


def repair_legacy_pin_issues(pin_mode: str, pin_values: Dict[str, int]) -> List[str]:
    defaults = DEFAULT_PIN_LAYOUTS[pin_mode]
    legacy_values = LEGACY_PIN_CANDIDATES.get(pin_mode, {})
    repair_messages: List[str] = []

    while True:
        issues = find_pin_issues(pin_mode, pin_values)
        if not issues:
            break

        repaired = False
        for fields, _ in issues:
            for field_name in fields:
                current_value = pin_values[field_name]
                default_value = defaults[field_name]
                if current_value == default_value:
                    continue

                if current_value not in legacy_values.get(field_name, set()):
                    continue

                pin_values[field_name] = default_value
                repair_messages.append(
                    f"Detected legacy {field_name}={current_value}. "
                    f"Auto-updating to {default_value} from pin_defaults.py."
                )
                repaired = True
                break
            if repaired:
                break

        if not repaired:
            break

    return repair_messages


def find_pin_issues(
    pin_mode: str, pin_fields: Dict[str, int]
) -> List[Tuple[Tuple[str, ...], str]]:
    issues: List[Tuple[Tuple[str, ...], str]] = []

    if pin_mode == "BOARD":
        for name, pin in pin_fields.items():
            if pin in NON_GPIO_BOARD_PINS:
                issues.append(
                    (
                        (name,),
                        f"{name}={pin} is power/GND in BOARD mode and cannot be used as GPIO.",
                    )
                )
    else:
        for name, pin in pin_fields.items():
            if pin not in HEADER_BCM_PINS:
                issues.append(
                    (
                        (name,),
                        f"{name}={pin} is not exposed on the 40-pin header in BCM mode.",
                    )
                )

    seen: Dict[int, str] = {}
    for name in PIN_FIELDS:
        pin = pin_fields[name]
        if pin in seen:
            issues.append(
                (
                    (seen[pin], name),
                    f"{name} reuses pin {pin}, already assigned to {seen[pin]}.",
                )
            )
        else:
            seen[pin] = name

    return issues


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

    pin_values = load_pin_values(pin_mode)

    config = AppConfig(
        api_key=api_key,
        model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview").strip(),
        decision_question=decision_question,
        room_description_prompt=os.getenv(
            "ROOM_DESCRIPTION_PROMPT",
            (
                "Describe the contents in the room in the most minimalistic manner "
                "such that Morse code is enough for the user to understand the scene."
            ),
        ).strip(),
        camera_index=int(os.getenv("CAMERA_INDEX", "0")),
        camera_device=os.getenv("CAMERA_DEVICE", "").strip(),
        camera_backends=os.getenv("CAMERA_BACKENDS", "V4L2,ANY").strip(),
        frame_width=int(os.getenv("FRAME_WIDTH", "1280")),
        frame_height=int(os.getenv("FRAME_HEIGHT", "720")),
        camera_warmup_frames=int(os.getenv("CAMERA_WARMUP_FRAMES", "8")),
        camera_read_attempts=int(os.getenv("CAMERA_READ_ATTEMPTS", "20")),
        save_capture=env_bool("SAVE_CAPTURE", True),
        capture_path=Path(os.getenv("CAPTURE_PATH", "captures/latest.jpg")),
        pin_mode=pin_mode,
        led_pin=pin_values["LED_PIN"],
        motor_pin=pin_values["MOTOR_PIN"],
        aux_led_pin=pin_values["AUX_LED_PIN"],
        mode_switch_pin=pin_values["MODE_SWITCH_PIN"],
        selector_switch_pin=pin_values["SELECTOR_SWITCH_PIN"],
        hcsr04_trigger_pin=pin_values["HCSR04_TRIGGER_PIN"],
        hcsr04_echo_pin=pin_values["HCSR04_ECHO_PIN"],
        switch_active_low=env_bool("SWITCH_ACTIVE_LOW", True),
        switch_debounce_seconds=float(os.getenv("SWITCH_DEBOUNCE_SECONDS", "0.25")),
        selector_medium_press_seconds=float(
            os.getenv("SELECTOR_MEDIUM_PRESS_SECONDS", "2.0")
        ),
        selector_long_press_seconds=float(
            os.getenv("SELECTOR_LONG_PRESS_SECONDS", "3.0")
        ),
        dot_seconds=float(os.getenv("DOT_SECONDS", "0.2")),
        repeat_same_morse=env_bool("REPEAT_SAME_MORSE", True),
        hcsr04_timeout_seconds=float(os.getenv("HCSR04_TIMEOUT_SECONDS", "0.03")),
        distance_threshold_cm=float(os.getenv("DISTANCE_THRESHOLD_CM", "150")),
        distance_min_period_seconds=float(
            os.getenv("DISTANCE_MIN_PERIOD_SECONDS", "0.08")
        ),
        distance_max_period_seconds=float(
            os.getenv("DISTANCE_MAX_PERIOD_SECONDS", "0.8")
        ),
        distance_read_interval_seconds=float(
            os.getenv("DISTANCE_READ_INTERVAL_SECONDS", "0.08")
        ),
        distance_log_interval_seconds=float(
            os.getenv("DISTANCE_LOG_INTERVAL_SECONDS", "0.5")
        ),
        main_loop_sleep_seconds=float(os.getenv("MAIN_LOOP_SLEEP_SECONDS", "0.03")),
    )
    validate_pin_config(config)
    return config


def validate_pin_config(config: AppConfig) -> None:
    pin_fields = {
        "LED_PIN": config.led_pin,
        "MOTOR_PIN": config.motor_pin,
        "AUX_LED_PIN": config.aux_led_pin,
        "MODE_SWITCH_PIN": config.mode_switch_pin,
        "SELECTOR_SWITCH_PIN": config.selector_switch_pin,
        "HCSR04_TRIGGER_PIN": config.hcsr04_trigger_pin,
        "HCSR04_ECHO_PIN": config.hcsr04_echo_pin,
    }
    issues = find_pin_issues(config.pin_mode, pin_fields)
    if issues:
        issue_lines = "\n".join(f"- {message}" for _, message in issues)
        current_map = ", ".join(f"{name}={pin_fields[name]}" for name in PIN_FIELDS)
        raise ValueError(
            "Invalid pin configuration:\n"
            f"{issue_lines}\n"
            f"Resolved pins: {current_map}\n"
            "Update your .env overrides or the defaults in pin_defaults.py."
        )

    if config.distance_threshold_cm <= 0:
        raise ValueError("DISTANCE_THRESHOLD_CM must be > 0.")
    if config.distance_min_period_seconds <= 0 or config.distance_max_period_seconds <= 0:
        raise ValueError("Distance period values must be > 0.")
    if config.distance_min_period_seconds > config.distance_max_period_seconds:
        raise ValueError(
            "DISTANCE_MIN_PERIOD_SECONDS must be <= DISTANCE_MAX_PERIOD_SECONDS."
        )
    if config.selector_medium_press_seconds <= config.switch_debounce_seconds:
        raise ValueError(
            "SELECTOR_MEDIUM_PRESS_SECONDS must be greater than SWITCH_DEBOUNCE_SECONDS."
        )
    if config.selector_long_press_seconds <= config.selector_medium_press_seconds:
        raise ValueError(
            "SELECTOR_LONG_PRESS_SECONDS must be greater than "
            "SELECTOR_MEDIUM_PRESS_SECONDS."
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


def request_gemini(config: AppConfig, url: str, payload: dict) -> dict:
    response = requests.post(
        url,
        json=payload,
        headers={"x-goog-api-key": config.api_key},
        timeout=30,
    )

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
            hint = " Forbidden. API key may be restricted or revoked."
        elif response.status_code == 404:
            hint = (
                f" Model not found or unavailable: {config.model}. "
                "Try an available model such as gemini-2.5-flash."
            )
        raise RuntimeError(
            f"Gemini API request failed with HTTP {response.status_code}.{hint} "
            f"Response body (truncated): {body}"
        )
    return response.json()


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


def ask_gemini_room_summary(config: AppConfig, image_bytes: bytes) -> str:
    url = f"{GEMINI_API_BASE}/{config.model}:generateContent"
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        f"{config.room_description_prompt}\n"
        "Rules:\n"
        "- Output only uppercase words separated by single spaces.\n"
        "- Use only letters A-Z, digits 0-9, and spaces.\n"
        "- No punctuation, bullets, or full sentences.\n"
        "- Keep it extremely short: 2 to 5 words.\n"
        "- Mention only the most important visible objects, furniture, people, or obstacles.\n"
        "- Prefer concrete nouns over adjectives and abstract words.\n"
        "- Order words by usefulness to a blind user entering the room.\n"
        "- Prefer navigationally important items first, such as PERSON, DOOR, TABLE, CHAIR, BED, STAIRS, BAG.\n"
        "- Skip colors, textures, and unimportant decoration.\n"
        "- If the scene is unclear, output UNKNOWN.\n"
        "Examples of valid outputs:\n"
        "BED CHAIR TABLE\n"
        "PERSON DESK LAPTOP\n"
        "DOOR WINDOW BAG"
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
            "temperature": 0.2,
            "maxOutputTokens": 64,
            "responseMimeType": "text/plain",
        },
    }

    data = request_gemini(config, url, payload)
    text = extract_text(data)
    if not text:
        raise ValueError(
            "Gemini returned no room description text. "
            f"finishReason={first_finish_reason(data)!r}, response={data}"
        )
    return normalize_morse_text(text)


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


def normalize_morse_text(text: str) -> str:
    normalized = text.upper().strip()
    normalized = re.sub(r"[^A-Z0-9\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValueError(f"Model did not return Morse-safe text. Raw response: {text!r}")
    return normalized


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
    GPIO.setup(config.aux_led_pin, GPIO.OUT, initial=GPIO.LOW)

    pull_mode = GPIO.PUD_UP if config.switch_active_low else GPIO.PUD_DOWN
    GPIO.setup(config.mode_switch_pin, GPIO.IN, pull_up_down=pull_mode)
    GPIO.setup(config.selector_switch_pin, GPIO.IN, pull_up_down=pull_mode)

    GPIO.setup(config.hcsr04_trigger_pin, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(config.hcsr04_echo_pin, GPIO.IN)

    time.sleep(0.05)


def set_morse_outputs(config: AppConfig, state: bool) -> None:
    value = GPIO.HIGH if state else GPIO.LOW
    GPIO.output(config.led_pin, value)
    GPIO.output(config.motor_pin, value)


def set_distance_outputs(config: AppConfig, state: bool) -> None:
    value = GPIO.HIGH if state else GPIO.LOW
    GPIO.output(config.motor_pin, value)
    GPIO.output(config.aux_led_pin, value)
    GPIO.output(config.led_pin, GPIO.LOW)


def clear_all_outputs(config: AppConfig) -> None:
    GPIO.output(config.led_pin, GPIO.LOW)
    GPIO.output(config.motor_pin, GPIO.LOW)
    GPIO.output(config.aux_led_pin, GPIO.LOW)


def signal_morse(config: AppConfig, morse: str) -> None:
    unit = config.dot_seconds
    words = morse.split(" / ")
    for word_index, word in enumerate(words):
        letters = word.split(" ")
        for letter_index, letter in enumerate(letters):
            for symbol_index, symbol in enumerate(letter):
                set_morse_outputs(config, True)
                time.sleep(unit if symbol == "." else 3 * unit)
                set_morse_outputs(config, False)
                if symbol_index < len(letter) - 1:
                    time.sleep(unit)

            if letter_index < len(letters) - 1:
                time.sleep(3 * unit)

        if word_index < len(words) - 1:
            time.sleep(7 * unit)


def is_switch_pressed(config: AppConfig, pin: int) -> bool:
    level = GPIO.input(pin)
    return level == GPIO.LOW if config.switch_active_low else level == GPIO.HIGH


def read_button_event(
    button_state: ButtonState, current_pressed: bool, now: float, debounce_seconds: float
) -> ButtonEvent:
    if current_pressed == button_state.prev_pressed:
        return ButtonEvent()
    if (now - button_state.last_event_at) < debounce_seconds:
        return ButtonEvent()

    button_state.last_event_at = now
    button_state.prev_pressed = current_pressed

    if current_pressed:
        button_state.press_started_at = now
        return ButtonEvent(pressed=True)

    duration = 0.0
    if button_state.press_started_at:
        duration = max(0.0, now - button_state.press_started_at)
    button_state.press_started_at = 0.0
    return ButtonEvent(released=True, press_duration=duration)


def classify_selector_press(config: AppConfig, press_duration: float) -> int:
    if press_duration >= config.selector_long_press_seconds:
        return 0
    if press_duration >= config.selector_medium_press_seconds:
        return 1
    return 2


def selector_press_label(config: AppConfig, selector_index: int) -> str:
    if selector_index == 0:
        return f"long press ({config.selector_long_press_seconds:.1f}s+)"
    if selector_index == 1:
        return (
            "medium press "
            f"({config.selector_medium_press_seconds:.1f}s-"
            f"{config.selector_long_press_seconds:.1f}s)"
        )
    return f"short press (<{config.selector_medium_press_seconds:.1f}s)"


def log_selector_menu(config: AppConfig) -> None:
    log(
        "Switch 2 selection menu: "
        f"long press -> Function 1, "
        f"medium press ({config.selector_medium_press_seconds:.1f}s-"
        f"{config.selector_long_press_seconds:.1f}s) -> Function 2, "
        f"short press -> Function 3."
    )


def measure_distance_cm(config: AppConfig) -> Optional[float]:
    GPIO.output(config.hcsr04_trigger_pin, GPIO.LOW)
    time.sleep(0.000002)
    GPIO.output(config.hcsr04_trigger_pin, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(config.hcsr04_trigger_pin, GPIO.LOW)

    timeout = config.hcsr04_timeout_seconds
    wait_start = time.monotonic()
    pulse_start = wait_start
    while GPIO.input(config.hcsr04_echo_pin) == GPIO.LOW:
        pulse_start = time.monotonic()
        if pulse_start - wait_start > timeout:
            return None

    pulse_end = pulse_start
    while GPIO.input(config.hcsr04_echo_pin) == GPIO.HIGH:
        pulse_end = time.monotonic()
        if pulse_end - pulse_start > timeout:
            return None

    duration = pulse_end - pulse_start
    distance_cm = (duration * 34300.0) / 2.0
    return max(distance_cm, 0.0)


def pulse_period_from_distance(config: AppConfig, distance_cm: float) -> float:
    clamped = min(max(distance_cm, 0.0), config.distance_threshold_cm)
    ratio = clamped / config.distance_threshold_cm
    return config.distance_min_period_seconds + ratio * (
        config.distance_max_period_seconds - config.distance_min_period_seconds
    )


def update_distance_alert_pulse(
    config: AppConfig, pulse_state: PulseState, now: float, distance_cm: Optional[float]
) -> None:
    if distance_cm is None or distance_cm >= config.distance_threshold_cm:
        if pulse_state.is_on:
            set_distance_outputs(config, False)
        pulse_state.is_on = False
        pulse_state.next_toggle_at = 0.0
        return

    period = pulse_period_from_distance(config, distance_cm)
    on_time = max(0.02, period * 0.5)
    off_time = max(0.02, period - on_time)

    if pulse_state.next_toggle_at == 0.0:
        pulse_state.is_on = True
        set_distance_outputs(config, True)
        pulse_state.next_toggle_at = now + on_time
        return

    if now >= pulse_state.next_toggle_at:
        pulse_state.is_on = not pulse_state.is_on
        set_distance_outputs(config, pulse_state.is_on)
        pulse_state.next_toggle_at = now + (on_time if pulse_state.is_on else off_time)


def run_vision_check(config: AppConfig) -> Tuple[str, str]:
    image = capture_frame(config)
    decision = ask_gemini_yes_no(config, image)
    morse = text_to_morse(decision)
    log(f"Vision result: {decision} | Morse: {morse}")
    return decision, morse


def run_room_description_check(config: AppConfig) -> Tuple[str, str]:
    image = capture_frame(config)
    summary = ask_gemini_room_summary(config, image)
    morse = text_to_morse(summary)
    log(f"Room summary: {summary} | Morse: {morse}")
    return summary, morse


def main() -> None:
    config = load_config()
    setup_gpio(config)

    mode_button = ButtonState(
        prev_pressed=is_switch_pressed(config, config.mode_switch_pin)
    )
    selector_button = ButtonState(
        prev_pressed=is_switch_pressed(config, config.selector_switch_pin)
    )

    mode1_enabled = False
    selector_index: Optional[int] = None
    selector_labels = [
        "Function 1: Vision Human Check",
        "Function 2: HC-SR04 Distance Alert",
        "Function 3: Room Summary To Morse",
    ]
    pulse_state = PulseState()
    last_decision: Optional[str] = None
    last_distance_read_at = 0.0
    last_distance_log_at = 0.0
    last_distance_cm: Optional[float] = None
    last_room_summary: Optional[str] = None

    log("System ready.")
    log(
        f"Switch 1 pin {config.mode_switch_pin}: Mode 1 toggle | "
        f"Switch 2 pin {config.selector_switch_pin}: press-duration selector"
    )
    log_selector_menu(config)

    try:
        while True:
            now = time.monotonic()
            mode_pressed = is_switch_pressed(config, config.mode_switch_pin)
            selector_pressed = is_switch_pressed(config, config.selector_switch_pin)
            mode_event = read_button_event(
                mode_button, mode_pressed, now, config.switch_debounce_seconds
            )
            selector_event = read_button_event(
                selector_button, selector_pressed, now, config.switch_debounce_seconds
            )

            if mode_event.pressed:
                mode1_enabled = not mode1_enabled
                state_text = "ENABLED" if mode1_enabled else "DISABLED"
                log(
                    f"Switch 1 (pin {config.mode_switch_pin}) pressed -> Mode 1 {state_text}"
                )
                clear_all_outputs(config)
                pulse_state = PulseState()
                selector_index = None
                last_distance_cm = None
                last_distance_read_at = 0.0
                last_distance_log_at = 0.0
                if mode1_enabled:
                    log("Mode 1 active.")
                    log_selector_menu(config)
                continue

            if selector_event.released:
                if not mode1_enabled:
                    log(
                        f"Switch 2 (pin {config.selector_switch_pin}) released after "
                        f"{selector_event.press_duration:.2f}s -> ignored (Mode 1 is disabled)"
                    )
                elif selector_index == 1:
                    log(
                        f"Switch 2 (pin {config.selector_switch_pin}) released after "
                        f"{selector_event.press_duration:.2f}s -> exiting navigation mode"
                    )
                    clear_all_outputs(config)
                    pulse_state = PulseState()
                    selector_index = None
                    last_distance_cm = None
                    last_distance_read_at = 0.0
                    last_distance_log_at = 0.0
                    log_selector_menu(config)
                    continue
                else:
                    selector_index = classify_selector_press(
                        config, selector_event.press_duration
                    )
                    log(
                        f"Switch 2 (pin {config.selector_switch_pin}) released after "
                        f"{selector_event.press_duration:.2f}s -> "
                        f"Selected {selector_labels[selector_index]} via "
                        f"{selector_press_label(config, selector_index)}"
                    )
                    clear_all_outputs(config)
                    pulse_state = PulseState()
                    if selector_index == 1:
                        last_distance_cm = None
                        last_distance_read_at = 0.0
                        last_distance_log_at = 0.0
                        log(
                            "Function 2 active: navigation mode running. "
                            "Click Switch 2 to exit navigation mode."
                        )
                    else:
                        try:
                            if selector_index == 0:
                                log(
                                    "Running Function 1: capture + Gemini human-presence check"
                                )
                                decision, morse = run_vision_check(config)
                                if (
                                    decision == last_decision
                                    and not config.repeat_same_morse
                                ):
                                    log(
                                        "Decision unchanged and REPEAT_SAME_MORSE=false, skipping repeated Morse."
                                    )
                                else:
                                    signal_morse(config, morse)
                                last_decision = decision
                            else:
                                log("Running Function 3: capture + Gemini room summary")
                                summary, morse = run_room_description_check(config)
                                if (
                                    summary == last_room_summary
                                    and not config.repeat_same_morse
                                ):
                                    log(
                                        "Room summary unchanged and REPEAT_SAME_MORSE=false, skipping repeated Morse."
                                    )
                                else:
                                    signal_morse(config, morse)
                                last_room_summary = summary
                        except Exception as exc:
                            log(f"Function {selector_index + 1} error: {exc}")
                        finally:
                            clear_all_outputs(config)
                            selector_index = None
                            log_selector_menu(config)
                        continue

            if not mode1_enabled or selector_index is None:
                time.sleep(config.main_loop_sleep_seconds)
                continue

            if selector_index == 1:
                if now - last_distance_read_at >= config.distance_read_interval_seconds:
                    last_distance_cm = measure_distance_cm(config)
                    last_distance_read_at = now

                update_distance_alert_pulse(config, pulse_state, now, last_distance_cm)

                if now - last_distance_log_at >= config.distance_log_interval_seconds:
                    if last_distance_cm is None:
                        log("Function 2: distance read timeout. Check HC-SR04 wiring.")
                    else:
                        alert_on = last_distance_cm < config.distance_threshold_cm
                        log(
                            f"Function 2: distance={last_distance_cm:.1f} cm | "
                            f"threshold={config.distance_threshold_cm:.1f} cm | "
                            f"alert={'ON' if alert_on else 'OFF'}"
                        )
                    last_distance_log_at = now

                time.sleep(config.main_loop_sleep_seconds)
    except KeyboardInterrupt:
        log("Keyboard interrupt received, shutting down.")
    finally:
        clear_all_outputs(config)
        GPIO.cleanup()


if __name__ == "__main__":
    main()
