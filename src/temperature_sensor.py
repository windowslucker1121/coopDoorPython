from abc import ABC, abstractmethod


class TemperatureSensor(ABC):
    """Abstract interface for all temperature/humidity sensor implementations.

    Both hardware sensors (DHT11, DHT22) and software sensors (API-based) must
    implement this interface, guaranteeing a uniform contract for the
    temperature task.
    """

    @abstractmethod
    def get_temperature_and_humidity(self) -> tuple:
        """Read temperature and humidity.

        Returns:
            (temp_fahrenheit, humidity_percent) — either value may be None on
            failure (sensor error, network timeout, etc.).
        """
        ...
