import logging

logger = logging.getLogger(__name__)

globalPins = {}
callbacks = {}

class MockGPIO:
    BCM = "BCM"
    IN = "IN"
    OUT = "OUT"
    HIGH = "HIGH"
    LOW = "LOW"
    PUD_DOWN = "PUD_DOWN"
    BOTH = "BOTH"
    RISING = "RISING"

    @staticmethod
    def setwarnings(state):
        pass

    @staticmethod
    def setmode(mode):
        logger.debug(f"GPIO mode set to {mode}")

    @staticmethod
    def setup(pin, mode, pull_up_down=None):
        globalPins[pin] = {"mode": mode, "state": MockGPIO.LOW}
        logger.debug(f"Pin {pin} set up as {mode} with pull {pull_up_down}")

    @staticmethod
    def output(pin, state):
        if pin in globalPins:
            globalPins[pin]["state"] = state
            # logger.debug(f"Pin {pin} set to {state}")
        else:
            raise ValueError(f"Pin {pin} is not set up.")

    @staticmethod
    def input(pin):
        return globalPins.get(pin, {}).get("state", MockGPIO.LOW)

    @staticmethod
    def add_event_detect(pin, edge=None, callback=None, bouncetime=0):
        edge = edge or MockGPIO.BOTH
        if callback:
            if pin not in callbacks:
                callbacks[pin] = []
            callbacks[pin].append(callback)
        logger.debug(f"Event detection added on pin {pin} for edge {edge} with bouncetime {bouncetime}")

    @staticmethod
    def trigger_event(pin, state):
        if pin in globalPins:
            globalPins[pin]["state"] = state
        if pin in callbacks:
            for cb in callbacks[pin]:
                cb(pin)

    @staticmethod
    def get_all_pins():
        return globalPins

    @staticmethod
    def cleanup():
        """Clear all pin state and registered callbacks.

        Call this between tests to prevent stale GPIO state and leftover
        callbacks from previous :class:`~door.DOOR` instances from
        interfering with subsequent tests.
        """
        globalPins.clear()
        callbacks.clear()

GPIO = MockGPIO()
