import random

class MockCPUTemperature:
    def __init__(self):
        self._temp = random.uniform(30.0, 70.0)

    @property
    def temperature(self):
        # Return a simulated slowly changing CPU temperature
        self._temp = max(30.0, min(70.0, self._temp + random.uniform(-1.0, 1.0)))
        return round(self._temp, 1)