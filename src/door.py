import os
from protected_dict import protected_dict as global_vars

if os.name == "nt":
    from mock_gpio import MockGPIO as GPIO
else:
    import RPi.GPIO as GPIO

GPIO.setwarnings(False)
import time

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
#Pin used for manual open override 
o_pin = 5
#Pin used for manual close override
c_pin = 6

class DOOR():
    def __init__(self):
        # Define state and pins used
        self.state = "stopped"
        self.override = False
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

    # Reference endstops and set them in the global_vars
    def reference_endstops(self):
        self.reference_door_active = True
        print("Referencing endstops - move door to closed position.")

        if os.name == "nt":
            print("Mocking endstops because we are on windows")
            self.reference_door_endstops_ms = 10000
            self.reference_door_active = False
            return
        
        # Move to close end stop
        self.close()
        while GPIO.input(end_down) != GPIO.HIGH:
            time.sleep(0.1)
    
        start_time = time.time()
        self.stop(state="closed")

        print("Referencing endstops - move door to open position.")
        # Move to open end stop
        self.open()
        while GPIO.input(end_up) != GPIO.HIGH:
            time.sleep(0.1)

        #get the time it took to move from closed to open in ms
        end_time = time.time()
        time_taken = end_time - start_time
        time_taken_ms = time_taken * 1000

        self.stop(state="open")

        # Set the reference_endstops_set variable
        self.reference_door_endstops_ms = time_taken_ms
        self.reference_door_active = False

    # Open or close door if switch activated:
    def switch_activated(self, channel):
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
        self.state = state

    def open(self):
        GPIO.output(in1, GPIO.LOW)
        GPIO.output(in2, GPIO.HIGH)
        GPIO.output(ena, GPIO.HIGH)
        self.state = "opening"

    def close(self):
        GPIO.output(in1, GPIO.HIGH)
        GPIO.output(in2, GPIO.LOW)
        GPIO.output(ena, GPIO.HIGH)
        self.state = "closing"

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

if __name__ == "__main__":
    door = DOOR()
    door.close_then_stop()
    door.open_then_stop()
