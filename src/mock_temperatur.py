import random

class MockCPUTemperature:
    def __init__(self):
        pass

    @property
    def temperature(self):
        # Return a simulated CPU temperature
        return round(random.uniform(30, 70), 1)