import threading

class ThreadSafeLoggerWriter:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.lock = threading.Lock()

    def write(self, message):
        if message.strip():
            with self.lock:
                self.logger.log(self.level, message.strip())

    def flush(self):
        pass