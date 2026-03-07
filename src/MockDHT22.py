import random
from temperature_sensor import TemperatureSensor

class MockDHT22(TemperatureSensor):
    def __init__(self, data_pin=None, power_pin=None):
        pass

    def get_temperature_and_humidity(self):
        # Simulate random temperature and humidity readings
        temp_c = round(random.uniform(20, 30), 1)  # Simulated temperature in °C
        temp_f = temp_c * (9.0 / 5.0) + 32.0       # Convert to °F (matches real DHT22)
        hum = round(random.uniform(30, 60), 1)      # Simulated humidity
        return temp_f, hum
