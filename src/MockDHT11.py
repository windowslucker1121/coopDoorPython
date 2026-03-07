import random
from temperature_sensor import TemperatureSensor

class MockDHT11(TemperatureSensor):
    def __init__(self, data_pin=None):
        pass

    def get_temperature_and_humidity(self):
        # Simulate random temperature and humidity readings (DHT11 integer resolution)
        temp_c = round(random.uniform(15, 35))       # Simulated temperature in °C (integer)
        temp_f = float(temp_c) * (9.0 / 5.0) + 32.0 # Convert to °F (matches real DHT11)
        hum = round(random.uniform(30, 70))          # Simulated humidity (integer %)
        return temp_f, float(hum)
