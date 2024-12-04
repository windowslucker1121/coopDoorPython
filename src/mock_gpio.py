globalPins = {}
class MockGPIO:
    BCM = "BCM"
    IN = "IN"
    OUT = "OUT"
    HIGH = "HIGH"
    LOW = "LOW"
    PUD_DOWN = "PUD_DOWN"
    BOTH = "BOTH"
    RISING = "RISING"

    def __init__(self):
        globalPins = {} 

    def setwarnings(state):
        pass

    def setmode(mode):
        print(f"GPIO mode set to {mode}")

    def setup(pin, mode, pull_up_down=None):
        globalPins[pin] = {"mode": mode, "state": MockGPIO.LOW}
        print(f"Pin {pin} set up as {mode} with pull {pull_up_down}")

    def output(pin, state):
        if pin in globalPins:
            globalPins[pin]["state"] = state
            # print(f"Pin {pin} set to {state}")
        else:
            raise ValueError(f"Pin {pin} is not set up.")

    def input(pin):
        return globalPins.get(pin, {}).get("state", MockGPIO.LOW)

    def add_event_detect(self, pin, edge=None, callback=None, bouncetime=0):
        edge = edge or MockGPIO.BOTH
        print(f"Event detection added on pin {pin} for edge {edge} with bouncetime {bouncetime}")

GPIO = MockGPIO()
