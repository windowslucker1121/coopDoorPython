import adafruit_dht
import RPi.GPIO as GPIO
import board
import time
import logging
from temperature_sensor import TemperatureSensor

logger = logging.getLogger(__name__)
class DHT22(TemperatureSensor):
    # Provide data pin and optional power pin. When a power_pin is supplied the
    # sensor power is cycled between reads to work around DHT22 lock-up issues.
    # Pass power_pin=None to skip power cycling (e.g. when the GPIO pin is
    # used for a different device).
    def __init__(self, data_pin, power_pin=None):
        self.pwr = int(power_pin) if power_pin is not None else None
        if self.pwr is not None:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pwr, GPIO.OUT)
            GPIO.output(self.pwr, GPIO.LOW)
        self.dht = adafruit_dht.DHT22(data_pin)

    def get_temperature_and_humidity(self):
        temp_f = None
        temp_c = None
        hum = None

        if self.pwr is not None:
            # Turn on power to sensor:
            GPIO.output(self.pwr, GPIO.HIGH)
            time.sleep(2.2)

        # Try up to 3 times:
        for idx in range(3):
            try:
                temp_c = self.dht.temperature
                hum = self.dht.humidity
                break
            except (RuntimeError, OverflowError):
                time.sleep(2.2)
                continue

        if self.pwr is not None:
            # Turn off power to sensor:
            GPIO.output(self.pwr, GPIO.LOW)

        if temp_c is not None:
            temp_f = temp_c * (9.0/5.0) + 32.0
        return temp_f, hum

if __name__ == '__main__':
    dht = DHT22(board.D21)
    while True:
        temp_f, hum = dht.get_temperature_and_humidity()
        logger.debug("Temperature={0:0.1f}F Humidity={1:0.1f}%".format(temp_f, hum))
        time.sleep(2.0)
