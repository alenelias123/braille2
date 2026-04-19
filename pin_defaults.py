PIN_FIELDS = (
    "LED_PIN",
    "MOTOR_PIN",
    "AUX_LED_PIN",
    "MODE_SWITCH_PIN",
    "SELECTOR_SWITCH_PIN",
    "HCSR04_TRIGGER_PIN",
    "HCSR04_ECHO_PIN",
)


DEFAULT_PIN_LAYOUTS = {
    "BOARD": {
        "LED_PIN": 11,
        "MOTOR_PIN": 36,
        "AUX_LED_PIN": 13,
        "MODE_SWITCH_PIN": 29,
        "SELECTOR_SWITCH_PIN": 31,
        "HCSR04_TRIGGER_PIN": 16,
        "HCSR04_ECHO_PIN": 18,
    },
    "BCM": {
        "LED_PIN": 17,
        "MOTOR_PIN": 16,
        "AUX_LED_PIN": 27,
        "MODE_SWITCH_PIN": 5,
        "SELECTOR_SWITCH_PIN": 6,
        "HCSR04_TRIGGER_PIN": 23,
        "HCSR04_ECHO_PIN": 24,
    },
}


LEGACY_PIN_CANDIDATES = {
    "BOARD": {
        "LED_PIN": {16},
        "MOTOR_PIN": {13},
        "AUX_LED_PIN": {19},
        "MODE_SWITCH_PIN": {12},
        "SELECTOR_SWITCH_PIN": {14, 15},
        "HCSR04_TRIGGER_PIN": {32},
        "HCSR04_ECHO_PIN": {34, 36},
    },
    "BCM": {},
}


NON_GPIO_BOARD_PINS = {1, 2, 4, 6, 9, 14, 17, 20, 25, 30, 34, 39}


HEADER_BCM_PINS = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
}
