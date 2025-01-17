import os
import logging
from protected_dict import protected_dict as global_vars

logger = logging.getLogger(__name__)


if os.name == "nt":
    from mock_gpio import MockGPIO as GPIO
else:
    import RPi.GPIO as GPIO

GPIO.setwarnings(False)
import time
#timeout where the reference sequence quits if it takes too long (in seconds)
referenceSequenceTimeout=60

#Pin used for up motion
in1 = 17
#Pin used for down motion
in2 = 27
#Pin used for powering the motor in general
ena = 22
#pin used for endstop up
end_up = 23
#pin used for endstop down
end_down = 24
#use this to invert the endstop up signal
invert_end_up = False
invert_end_down = False

#Pin used for manual open override 
o_pin = 5
#Pin used for manual close override
c_pin = 6

class DOOR():
    def __init__(self):
        # Define state and pins used
        self.state = "stopped"
        self.lastState = "stopped"
        self.override = False
        self.errorState = None
        self.reference_door_endstops_ms = None
        self.reference_door_active = False
        self.startedMovingTime = None
        self.timeOutWindowClosingDoor = 0.5
        self.auto_mode = None

        # Set up motor controller:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(in1, GPIO.OUT)
        GPIO.setup(in2, GPIO.OUT)
        GPIO.setup(ena, GPIO.OUT)
        self.stop()

        # Set up switch detection:
        GPIO.setup(o_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(c_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        
        GPIO.add_event_detect(o_pin, GPIO.BOTH, callback=self.switch_activated, bouncetime=600)
        GPIO.add_event_detect(c_pin, GPIO.BOTH, callback=self.switch_activated, bouncetime=600)

        GPIO.setup(end_up, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(end_down, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        GPIO.add_event_detect(end_up, GPIO.BOTH, callback=self.endstop_hit, bouncetime=250)
        GPIO.add_event_detect(end_down, GPIO.BOTH, callback=self.endstop_hit, bouncetime=250)

    def clear_errorState(self):
        self.errorState = None
        logger.info("Error state cleared")

    def ErrorState(self, state=None, stopDoor: bool = True):
        if state is not None and state != self.errorState:
            if stopDoor:
                self.stop (str(state))
            self.errorState = state
            logger.critical("Error state set to: " + str(state) + " - Stopping all motor activity until error is cleared.")
            return True
        elif self.errorState:
            return True
        return False
    # Reference endstops and set them in the global_vars
    def reference_endstops(self) -> bool:

        if self.ErrorState():
            logger.critical("Error state detected, cannot reference endstops")
            self.reference_door_active = False
            return False
        
        self.reference_door_active = True
        logger.info(f"Referencing - Timeout set to {referenceSequenceTimeout} seconds.")
        logger.debug(f"Referencing - move door to CLOSE position.")

        if os.name == "nt":
            logger.info("Mocking endstops because we are on windows")
            self.reference_door_endstops_ms = 10000
            self.reference_door_active = False
            return True
        
        compareValueLower = GPIO.LOW if invert_end_down else GPIO.HIGH
        compareValueUpper = GPIO.LOW if invert_end_up else GPIO.HIGH
        sequenceStartedTime = time.time()
        sequenceTimeoutTime = sequenceStartedTime + referenceSequenceTimeout
        logger.debug("Current time %s - Timeout time %s", sequenceStartedTime, sequenceTimeoutTime)

        #check if the endstop is already hit
        if GPIO.input(end_down) == compareValueLower:
            logger.error("Endstop already hit, stopping motor")
            self.reference_door_active = False
            return False
        
        if GPIO.input(end_up) == compareValueUpper:
            logger.error("Endstop already hit, stopping motor")
            self.reference_door_active = False
            return False
        logger.debug("Setting motor to close...")
        
        self.close()
        validEndstop = False
        while not validEndstop:
            validEndstop = GPIO.input(end_down) == compareValueLower

            if validEndstop:
                time.sleep(0.1)
                validEndstop = GPIO.input(end_down) == compareValueLower
                if validEndstop:
                    break

            # logger.debug("Waiting for endstop to be hit - current Endstop Value:" + str(GPIO.input(end_down)))
            if time.time() - sequenceStartedTime > referenceSequenceTimeout:
                self.reference_door_active = False
                self.ErrorState("Reference sequence timed out - lower Endstop not hit")
                return False
            time.sleep(0.1)

        self.stop(state="closed")
        logger.debug("Endstop hit, stopping motor")
        start_time = time.time()

        logger.debug("Referencing - move door to OPEN position.")
        # Move to open end stop
        logger.debug("Setting motor to open...")
        sequenceStartedTime = time.time()
        sequenceTimeoutTime = sequenceStartedTime + referenceSequenceTimeout
        logger.debug("Current time %s - Timeout time %s", sequenceStartedTime, sequenceTimeoutTime)
        self.open()

        validEndstop = False
        while not validEndstop:
            validEndstop = GPIO.input(end_up) == compareValueUpper

            if validEndstop:
                time.sleep(0.1)
                validEndstop = GPIO.input(end_up) == compareValueUpper
                if validEndstop:
                    break
            
            # logger.debug("Waiting for endstop to be hit - current Endstop Value:" + str(GPIO.input(end_up)))
            if time.time() - sequenceStartedTime > referenceSequenceTimeout:
                self.reference_door_active = False
                self.ErrorState("Reference sequence timed out - upper Endstop not hit")
                return False
            time.sleep(0.1)

        logger.debug("Endstop hit, stopping motor")
        #get the time it took to move from closed to open in ms
        end_time = time.time()
        time_taken = end_time - start_time
        time_taken_ms = time_taken * 1000

        self.stop(state="open")

        # Set the reference_endstops_set variable
        self.reference_door_endstops_ms = time_taken_ms
        self.reference_door_active = False
        logger.info("Referenced endstops in " + str(time_taken_ms) + "ms")
        return True

    #endstop is hit, stop the motor
    def endstop_hit(self, channel):
        if self.reference_door_active:
            return
        time.sleep(0.2)

        compareValueUpper = GPIO.LOW if invert_end_up else GPIO.HIGH
        compareValueLower = GPIO.LOW if invert_end_down else GPIO.HIGH

        endstopUpper = GPIO.input(end_up)
        endstopLower = GPIO.input(end_down)
        if endstopUpper == compareValueUpper:
            self.stop(state="open")
        elif endstopLower == compareValueLower:
            self.stop(state="closed")
        logger.info("Endstops changed - Upper: %s - Lower: %s", endstopUpper, endstopLower)
        
    # Open or close door if switch activated:
    def switch_activated(self, channel):
        if self.ErrorState():
            return
        
        if self.reference_door_active:
            return
        # Wait just a bit for stability, so we make sure we get
        # a good reading right after the interrupt.
        time.sleep(0.15)
        o_read = GPIO.input(o_pin)
        c_read = GPIO.input(c_pin)
        if o_read != c_read:
            if o_read == GPIO.HIGH:
                #logger.debug("Opening!")
                logger.debug("Manuel Switch to UP activated.")
                self.override = True
                self.open()
            elif c_read == GPIO.HIGH:
                #logger.debug("Closing!")
                logger.debug("Manuel Switch to DOWN activated.")
                self.override = True
                self.close()

    # When called, stops door if switch is neutral.
    def check_if_switch_neutral(self, nuetral_state="stopped"):
        if self.ErrorState():
            return
        if self.reference_door_active:
            return
        # Wait just a bit for stability:
        o_read = GPIO.input(o_pin)
        c_read = GPIO.input(c_pin)
        if o_read == c_read:
            #logger.debug("Do nothing.")
            self.override = False
            self.stop(state=nuetral_state)

    def get_state(self):
        return self.state

    def get_override(self):
        return self.override

    def set_auto_mode(self, auto_mode):
        self.auto_mode = auto_mode

    def stop(self, state="stopped"):
        GPIO.output(in1, GPIO.LOW)
        GPIO.output(in2, GPIO.LOW)
        GPIO.output(ena, GPIO.LOW)
        if self.lastState != state:
            timeStopped = time.time() - self.startedMovingTime
            logger.debug("Door stopped and is in state: " + state + " - Time stopped: " + str(timeStopped) + " AutoMode: " + str(self.auto_mode) + " Override: " + str(self.override))
            if self.reference_door_endstops_ms is not None and self.auto_mode is True and self.override is not True:
                lower_bound = self.reference_door_endstops_ms / 1000 - self.timeOutWindowClosingDoor / 2
                upper_bound = self.reference_door_endstops_ms / 1000 + self.timeOutWindowClosingDoor / 2
                logger.debug(f"Door stopped and is in state: {state} - Time moved: {timeStopped:.2f} s - Time window: {lower_bound:.2f} s - {upper_bound:.2f} s")
                if timeStopped < lower_bound or timeStopped > upper_bound:
                    logger.error(
                        f"Time stopped ({timeStopped:.2f} s) is outside the range (reference_door_endstops_ms: {self.reference_door_endstops_ms:.2f} s) - Door might be stuck"
                        f"({lower_bound:.2f} s - {upper_bound:.2f} s) - Door might be stuck"
                    )
                    self.ErrorState("Door might be stuck", stopDoor=False)
            logger.info("GPIO - Door stopped and is in state: " + state)
            self.lastState = state
        self.state = state
        

    def open(self):
        if self.ErrorState():
            return
        upperEndStopTriggered = GPIO.input(end_up)
        compareValue = GPIO.LOW if invert_end_up else GPIO.HIGH

        if upperEndStopTriggered == compareValue:
            self.stop(state="open")
            return
        

        GPIO.output(in1, GPIO.LOW)
        GPIO.output(in2, GPIO.LOW)
        GPIO.output(ena, GPIO.HIGH)

        self.state = "opening"

        if self.lastState != self.state:
            self.startedMovingTime = time.time()
            logger.info("GPIO - Door opening")
            self.lastState = self.state
        

    def close(self):
        if self.ErrorState():
            return
        
        lowerEndStopTriggered = GPIO.input(end_down)
        compareValue = GPIO.LOW if invert_end_down else GPIO.HIGH

        if lowerEndStopTriggered == compareValue:
            self.stop(state="closed")
            return
        

        GPIO.output(in1, GPIO.HIGH)
        GPIO.output(in2, GPIO.HIGH)
        GPIO.output(ena, GPIO.HIGH)
        self.state = "closing"

        if self.lastState != self.state:
            self.startedMovingTime = time.time()
            self.lastState = self.state
            logger.info("GPIO - Door closing")

    def open_then_stop(self):
        self.open()
        time.sleep(30)
        self.stop()
        self.state = "open"

    def close_then_stop(self):
        self.close()
        time.sleep(30)
        self.stop()
        self.state = "closed"

    def __del__(self):
        self.stop()

if __name__ == "__main__":
    door = DOOR()
    door.reference_endstops()
