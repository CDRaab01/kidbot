"""Tests for pi_client/button.py — PushToTalkButton GPIO dispatch."""
import importlib
import sys
import time
from unittest.mock import MagicMock, patch


def _import_button():
    import pi_client.button as button
    importlib.reload(button)
    return button


def _wait_for(predicate, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestSetup:
    def test_sets_button_and_led_pins(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        gpio.setup.reset_mock()
        button.PushToTalkButton()
        setup_pins = {c[0][0] for c in gpio.setup.call_args_list}
        assert button.BUTTON_PIN in setup_pins
        assert button.LED_PIN in setup_pins

    def test_registers_both_edge_detection(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        gpio.add_event_detect.reset_mock()
        button.PushToTalkButton()
        gpio.add_event_detect.assert_called_once()
        assert gpio.add_event_detect.call_args[0][0] == button.BUTTON_PIN


class TestDispatch:
    def test_low_level_fires_press_callbacks(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        b = button.PushToTalkButton()
        pressed = []
        b.on_press(lambda: pressed.append(True))
        gpio.input.return_value = gpio.LOW
        b._gpio_event(button.BUTTON_PIN)
        assert _wait_for(lambda: pressed)

    def test_high_level_fires_release_callbacks(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        b = button.PushToTalkButton()
        released = []
        b.on_release(lambda: released.append(True))
        gpio.input.return_value = gpio.HIGH
        b._gpio_event(button.BUTTON_PIN)
        assert _wait_for(lambda: released)


class TestLed:
    def test_led_on(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        b = button.PushToTalkButton()
        gpio.output.reset_mock()
        b.led(True)
        gpio.output.assert_called_with(button.LED_PIN, gpio.HIGH)

    def test_led_off(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        b = button.PushToTalkButton()
        gpio.output.reset_mock()
        b.led(False)
        gpio.output.assert_called_with(button.LED_PIN, gpio.LOW)


class TestCleanup:
    def test_cleanup_removes_event_detect(self):
        button = _import_button()
        gpio = sys.modules["RPi.GPIO"]
        b = button.PushToTalkButton()
        gpio.remove_event_detect.reset_mock()
        b.cleanup()
        gpio.remove_event_detect.assert_called_once_with(button.BUTTON_PIN)
