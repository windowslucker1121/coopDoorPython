import random

class MockDHT11:
    def __init__(self, data_pin=None):
        pass

    def get_temperature_and_humidity(self):
        # Simulate random temperature and humidity readings (DHT11 integer resolution)
        temp = round(random.uniform(15, 35))   # Simulated temperature (integer °C)
        hum = round(random.uniform(30, 70))    # Simulated humidity (integer %)
        return float(temp), float(hum)
