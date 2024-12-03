import random

class MockDHT22:
    def __init__(self, data_pin=None, power_pin=None):
        pass

    def get_temperature_and_humidity(self):
        # Simulate random temperature and humidity readings
        temp = round(random.uniform(20, 30), 1)  # Simulated temperature
        hum = round(random.uniform(30, 60), 1)   # Simulated humidity
        return temp, hum
