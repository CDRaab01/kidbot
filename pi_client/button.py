import threading
import time
import RPi.GPIO as GPIO
from .config import BUTTON_PIN, LED_PIN


class PushToTalkButton:
    def __init__(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(LED_PIN, GPIO.OUT)
        GPIO.output(LED_PIN, GPIO.LOW)

        self._press_callbacks: list = []
        self._release_callbacks: list = []

        GPIO.add_event_detect(
            BUTTON_PIN,
            GPIO.BOTH,
            callback=self._gpio_event,
            bouncetime=50,
        )

    def _gpio_event(self, channel):
        if GPIO.input(BUTTON_PIN) == GPIO.LOW:
            for cb in self._press_callbacks:
                threading.Thread(target=cb, daemon=True).start()
        else:
            for cb in self._release_callbacks:
                threading.Thread(target=cb, daemon=True).start()

    def on_press(self, callback):
        self._press_callbacks.append(callback)

    def on_release(self, callback):
        self._release_callbacks.append(callback)

    def led(self, state: bool):
        GPIO.output(LED_PIN, GPIO.HIGH if state else GPIO.LOW)

    def blink(self, count: int = 3, interval: float = 0.2):
        for _ in range(count):
            GPIO.output(LED_PIN, GPIO.HIGH)
            time.sleep(interval)
            GPIO.output(LED_PIN, GPIO.LOW)
            time.sleep(interval)

    def cleanup(self):
        try:
            GPIO.remove_event_detect(BUTTON_PIN)
        except Exception:
            pass
