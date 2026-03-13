import random
from temperature_sensor import TemperatureSensor

class MockDHT11(TemperatureSensor):
    def __init__(self, data_pin=None):
        self.temp_c = random.uniform(15, 35)
        self.hum = random.uniform(30, 70)

    def get_temperature_and_humidity(self):
        # Simulate small changes
        self.temp_c = max(15.0, min(35.0, self.temp_c + random.uniform(-1, 1)))
        temp_f = float(round(self.temp_c)) * (9.0 / 5.0) + 32.0
        self.hum = max(30.0, min(70.0, self.hum + random.uniform(-2, 2)))
        return temp_f, float(round(self.hum))
