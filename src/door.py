import os
from protected_dict import protected_dict as global_vars

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

        GPIO.add_event_detect(end_up, GPIO.BOTH, callback=self.endstop_hit, bouncetime=500)
        GPIO.add_event_detect(end_down, GPIO.BOTH, callback=self.endstop_hit, bouncetime=500)

    def clear_errorState(self):
        self.errorState = None
        print("Error state cleared")

    def ErrorState(self, state=None):
        if state is not None and state != self.errorState:
            self.stop (str(state))
            self.errorState = state
            print("Error state set to: " + str(state) + " - Stopping all motor activity until error is cleared.")
            return True
        elif self.errorState:
            return True
        return False
    # Reference endstops and set them in the global_vars
    def reference_endstops(self) -> bool:

        if self.ErrorState():
            print("Error state detected, cannot reference endstops")
            self.reference_door_active = False
            return False
        
        self.reference_door_active = True
        print("Referencing - move door to CLOSE position.")

        if os.name == "nt":
            print("Mocking endstops because we are on windows")
            self.reference_door_endstops_ms = 10000
            self.reference_door_active = False
            return True
        
        compareValueLower = GPIO.LOW if invert_end_down else GPIO.HIGH
        compareValueUpper = GPIO.LOW if invert_end_up else GPIO.HIGH
        sequenceStartedTime = time.time()
        print("Setting motor to close...")
        
        self.close()
        while GPIO.input(end_down) != compareValueLower:
            # print("Waiting for endstop to be hit - current Endstop Value:" + str(GPIO.input(end_down)))
            if time.time() - sequenceStartedTime > referenceSequenceTimeout:
                print("Reference sequence timed out - lower Endstop not hit")
                self.reference_door_active = False
                return False
            time.sleep(0.1)
    
        print("Endstop hit, stopping motor")
        start_time = time.time()
        self.stop(state="closed")

        print("Referencing - move door to OPEN position.")
        # Move to open end stop
        print("Setting motor to open...")
        sequenceStartedTime = time.time()
        self.open()
        while GPIO.input(end_up) != compareValueUpper:
            # print("Waiting for endstop to be hit - current Endstop Value:" + str(GPIO.input(end_up)))
            if time.time() - sequenceStartedTime > referenceSequenceTimeout:
                print("Reference sequence timed out - upper Endstop not hit")
                self.reference_door_active = False
                return False
            time.sleep(0.1)

        print("Endstop hit, stopping motor")
        #get the time it took to move from closed to open in ms
        end_time = time.time()
        time_taken = end_time - start_time
        time_taken_ms = time_taken * 1000

        self.stop(state="open")

        # Set the reference_endstops_set variable
        self.reference_door_endstops_ms = time_taken_ms
        self.reference_door_active = False
        print("Referenced endstops in " + str(time_taken_ms) + "ms")
        return True

    #endstop is hit, stop the motor
    def endstop_hit(self, channel):
        if self.reference_door_active:
            return
        time.sleep(0.1)

        compareValueUpper = GPIO.LOW if invert_end_up else GPIO.HIGH
        compareValueLower = GPIO.LOW if invert_end_down else GPIO.HIGH

        endstopUpper = GPIO.input(end_up)
        endstopLower = GPIO.input(end_down)
        if endstopUpper == compareValueUpper:
            self.stop(state="open")
        elif endstopLower == compareValueLower:
            self.stop(state="closed")
        
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
                #print("Opening!")
                self.override = True
                self.open()
            elif c_read == GPIO.HIGH:
                #print("Closing!")
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
            #print("Do nothing.")
            self.override = False
            self.stop(state=nuetral_state)

    def get_state(self):
        return self.state

    def get_override(self):
        return self.override

    def stop(self, state="stopped"):
        GPIO.output(in1, GPIO.LOW)
        GPIO.output(in2, GPIO.LOW)
        GPIO.output(ena, GPIO.LOW)
        if self.lastState != state:
            print("GPIO - Door stopped and is in state: " + state)
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
            print("GPIO - Door opening")
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
            self.lastState = self.state
            print("GPIO - Door closing")

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
