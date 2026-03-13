import random
from temperature_sensor import TemperatureSensor

class MockDHT22(TemperatureSensor):
    def __init__(self, data_pin=None, power_pin=None):
        self.temp_c = random.uniform(20, 30)
        self.hum = random.uniform(30, 60)

    def get_temperature_and_humidity(self):
        # Simulate small changes
        self.temp_c = max(20.0, min(30.0, self.temp_c + random.uniform(-0.5, 0.5)))
        temp_c_rounded = round(self.temp_c, 1)
        temp_f = temp_c_rounded * (9.0 / 5.0) + 32.0       # Convert to °F (matches real DHT22)
        
        self.hum = max(30.0, min(60.0, self.hum + random.uniform(-1.0, 1.0)))
        hum_rounded = round(self.hum, 1)
        return temp_f, hum_rounded
