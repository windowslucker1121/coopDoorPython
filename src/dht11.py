import adafruit_dht
import time
import logging

logger = logging.getLogger(__name__)

class DHT11():
    """Wrapper around adafruit_dht.DHT11.

    Unlike DHT22 this sensor does not require GPIO power-cycling, so no
    power pin is needed.  Temperature is returned in Fahrenheit together
    with humidity percentage.
    """

    def __init__(self, data_pin):
        self.dht = adafruit_dht.DHT11(data_pin)

    def get_temperature_and_humidity(self):
        temp_f = None
        temp_c = None
        hum = None

        # Try up to 3 times:
        for _ in range(3):
            try:
                temp_c = self.dht.temperature
                hum = self.dht.humidity
                break
            except (RuntimeError, OverflowError):
                time.sleep(2.2)
                continue

        if temp_c is not None:
            temp_f = temp_c * (9.0 / 5.0) + 32.0
        return temp_f, hum
