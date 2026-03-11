import adafruit_dht
import time
import logging
from temperature_sensor import TemperatureSensor

logger = logging.getLogger(__name__)

class DHT11(TemperatureSensor):
    """Wrapper around adafruit_dht.DHT11.

    Unlike DHT22 this sensor does not require GPIO power-cycling, so no
    power pin is needed.  Temperature is returned in Fahrenheit together
    with humidity percentage.
    """

    def __init__(self, data_pin):
        self._data_pin = data_pin
        self.dht = adafruit_dht.DHT11(data_pin)

    def _reinit(self):
        """Exit and recreate the underlying adafruit_dht object to recover from
        a stuck PulseIn state (OSError [Errno 22])."""
        try:
            self.dht.exit()
        except Exception:
            pass
        time.sleep(2.2)
        self.dht = adafruit_dht.DHT11(self._data_pin)

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
            except OSError:
                # PulseIn entered a bad state — reinitialise to recover.
                logger.warning("DHT11: OSError on read, reinitialising sensor")
                self._reinit()
                continue

        if temp_c is not None:
            temp_f = temp_c * (9.0 / 5.0) + 32.0
        return temp_f, hum
