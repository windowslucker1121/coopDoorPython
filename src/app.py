#this needs to be at the top of the gevent because the threads somehow interfere - i dont know why but this is the solution for now
from door import DOOR
from gevent import monkey
monkey.patch_all()
from flask import Flask, render_template, Response, send_file, request, jsonify
from threading import Thread, Lock
from flask_socketio import SocketIO
from datetime import datetime, date, timedelta
from protected_dict import protected_dict as global_vars
from astral import LocationInfo
from astral.geocoder import database, LocationDatabase
from astral.sun import sun
from gpiozero import CPUTemperature
import time
import psutil
import json
import pytz
import ruamel.yaml as YAML
import os.path
from collections import deque
import logging
import sys
from camera import Camera
import base64
from pywebpush import webpush, WebPushException
import atexit

if os.name != 'nt':
    from gpiozero import CPUTemperature
    import board
    from dht22 import DHT22
else:
    print("Running on Windows, using mock classes.")
    from MockDHT22 import MockDHT22
    from mock_board import MockBoard
    from mock_temperatur import MockCPUTemperature
    CPUTemperature = MockCPUTemperature
    DHT22 = MockDHT22
    board = MockBoard()


##################################
# Flask configuration:
##################################


app = Flask(__name__, template_folder="templates")
app.config['SECRET_KEY'] = 'secret_key'
socketio = SocketIO(app, async_mode='gevent')

log_buffer = deque(maxlen=100)
camera = None
##################################
# Helper functions:
##################################

# Get system uptime in seconds
uptime_seconds = psutil.boot_time()
uptime_datetime = datetime.fromtimestamp(uptime_seconds)

def get_uptime():
    # Calculate the time difference between now and the uptime
    uptime_delta = datetime.now() - uptime_datetime

    # Extract days, hours, minutes, and seconds from the time difference
    days = uptime_delta.days
    hours, remainder = divmod(uptime_delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Return system uptime string
    return f"{days} day(s), {hours} hour(s), {minutes} minute(s), {seconds} second(s)"

# Define the location of Boulder, Colorado
boulder = LocationInfo("Boulder", "USA", "America/Denver", 40.01499, -105.27055)
timezone = pytz.timezone('America/Denver')

def get_sunrise_and_sunset():
    # Get the sunrise and sunset times for today
    s = sun(boulder.observer, date=date.today(), tzinfo=boulder.timezone)

    # Convert sunrise and sunset to the desired timezone (e.g., 'America/Denver')
    return s["sunrise"].astimezone(timezone), s["sunset"].astimezone(timezone)

def get_current_time():
    return timezone.localize(datetime.now())

root_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
config_filename = os.path.join(root_path, "config.yaml")
config_lock = Lock()

def save_config():
    with config_lock:
        with open(config_filename, 'w') as file:
            yaml = YAML.YAML()
            to_dump = {
                "auto_mode": global_vars.instance().get_value("auto_mode"),
                "sunrise_offset": global_vars.instance().get_value("sunrise_offset"),
                "sunset_offset": global_vars.instance().get_value("sunset_offset"),
                "location": global_vars.instance().get_value("location"),
                "csvLog": global_vars.instance().get_value("csvLog"),
                "enable_camera" : global_vars.instance().get_value("enable_camera"),
                "camera_index" : global_vars.instance().get_value("camera_index")
            }
            yaml.dump(to_dump, file)

def load_config():
    saveNewConfig = False
    with config_lock:
        config_to_set = {
            "auto_mode": "True",
            "sunrise_offset": 0,
            "sunset_offset": 0,
            "location": {
                "city": "Boulder",
                "region": "USA",
                "timezone": "America/Denver",
                "latitude": 40.01499,
                "longitude": -105.27055
            },
            #will be used for currently not implemented stopping/starting of the logging thread
            "csvLog": True,
            "enable_camera" : False,
            "camera_index" : 0
            
        }
        if os.path.exists(config_filename):
            with open(config_filename, 'r') as file:
                yaml = YAML.YAML()
                content = file.read()
                yaml_config = yaml.load(content)
                config_to_set.update(yaml_config)
        else:
            saveNewConfig = True

        global_vars.instance().set_values(config_to_set)

    if saveNewConfig:
        print("No configuration file found, creating a new one.")
        save_config()


def get_valid_locations() -> list:
    locations = []
    location_database = database()
    #print(type(location_database))

    for location_name, location_info in location_database.items():
        if isinstance(location_info, dict):
            for sub_location_name, sub_location_info in location_info.items():
                locations.append({
                    "name": f"{location_name} - {sub_location_name}",
                    "region": sub_location_info[0].region,
                    "timezone": sub_location_info[0].timezone,
                    "latitude": sub_location_info[0].latitude,
                    "longitude": sub_location_info[0].longitude
                })
        locations.sort(key=lambda x: (x['name'], x['region']))
    return locations

def reload_location_data():
    location = global_vars.instance().get_value("location")
    global boulder, timezone

    boulder = LocationInfo(
        location["city"],
        location["region"],
        location["timezone"],
        location["latitude"],
        location["longitude"]
    )
    timezone = pytz.timezone(location["timezone"])


def get_all_data():
    # Grab data safely from global store:
    temp_in, hum_in, temp_out, hum_out, state, override, cpu_temp, \
        sunrise, sunset, sunrise_offset, sunset_offset, \
        temp_in_min, temp_in_max, hum_in_min, hum_in_max, \
        temp_out_min, temp_out_max, hum_out_min, hum_out_max, \
        cpu_temp_min, cpu_temp_max, reference_door_endstops_ms, auto_mode, error_state, camera_enabled\
        = global_vars.instance().get_values(["temp_in", "hum_in", \
            "temp_out", "hum_out", "state", "override", "cpu_temp", \
            "sunrise", "sunset", "sunrise_offset", "sunset_offset", \
            "temp_in_min", "temp_in_max", "hum_in_min", "hum_in_max", \
            "temp_out_min", "temp_out_max", "hum_out_min", "hum_out_max", \
            "cpu_temp_min", "cpu_temp_max", \
            "reference_door_endstops_ms", "auto_mode" , "error_state", "enable_camera"])

    # Check if time until sunrise is positive
    time_until_open_str = None
    time_until_close_str = None

    if auto_mode == "False":
        time_until_open_str = "disabled"
        time_until_close_str = "disabled"
    elif sunrise is not None and sunset is not None:
        # Assuming sunrise and sunset are datetime objects
        current_time = get_current_time()
        time_until_open = sunrise + timedelta(minutes=sunrise_offset) - current_time
        time_until_close = sunset + timedelta(minutes=sunset_offset) - current_time

        if time_until_open > timedelta(0):
            time_until_open_str = (datetime.min + time_until_open).strftime("%H:%M:%S")
        else:
            time_until_open_str = "passed"

        # Check if time until sunset is positive
        if time_until_close > timedelta(0):
            time_until_close_str = (datetime.min + time_until_close).strftime("%H:%M:%S")
        else:
            time_until_close_str = "passed"

    def format_temp(temp, units="F"):
        return ("%0.1f" % temp) + u'\N{DEGREE SIGN}' + units if temp is not None else ""

    def format_hum(hum):
        return "%0.1f%%" % hum if hum is not None else ""

    # Return nicely formatted data in dictionary form:
    data_dict = {
      'time': datetime.now().strftime("%I:%M:%S.%f %p")[:-3],
      'temp_in': format_temp(temp_in),
      'temp_in_min': format_temp(temp_in_min),
      'temp_in_max': format_temp(temp_in_max),
      'hum_in': format_hum(hum_in),
      'hum_in_min': format_hum(hum_in_min),
      'hum_in_max': format_hum(hum_in_max),
      'temp_out': format_temp(temp_out),
      'temp_out_min': format_temp(temp_out_min),
      'temp_out_max': format_temp(temp_out_max),
      'hum_out': format_hum(hum_out),
      'hum_out_min': format_hum(hum_out_min),
      'hum_out_max': format_hum(hum_out_max),
      'cpu_temp': format_temp(cpu_temp, units="C"),
      'cpu_temp_min': format_temp(cpu_temp_min, units="C"),
      'cpu_temp_max': format_temp(cpu_temp_max, units="C"),
      'state': state if state is not None else "",
      'override': state if state is not None and override else "off",
      'uptime': str(get_uptime()),
      'sunrise': sunrise.strftime("%I:%M:%S %p").lstrip('0') if sunrise is not None else "",
      'sunset': sunset.strftime("%I:%M:%S %p").lstrip('0') if sunset is not None else "",
      'tu_open': time_until_open_str if time_until_open_str is not None else "",
      'tu_close': time_until_close_str if time_until_close_str is not None else "",
      'reference_door_endstops_ms': str(reference_door_endstops_ms) if reference_door_endstops_ms is not None else "Not set",
      'auto_mode': auto_mode,
      'errorstate' : error_state,
      'camera_enabled' : str(camera_enabled)
    }
    return data_dict

##################################
# Background tasks:
##################################

def temperature_task():
    data_pin_out = board.D21
    data_pin_in = board.D16
    dht_out = DHT22(data_pin_out, power_pin=20)
    dht_in = DHT22(data_pin_in, power_pin=26)
    last_date = None

    # Update value in global vars, and also store min and max seen since startup:
    def update_val(val, name):
        if val is not None:
            # Get current vals
            val_old, val_max, val_min = \
                global_vars.instance().get_values([name, name + "_max", name + "_min"])

            # Throw away any errant readings. Sometimes the readings are nonsensical
            if val_old is not None:
                if val > (val_old + 5.0) or val < (val_old - 5.0):
                    val = val_old

            # Update min and max
            val_max = val_max if val_max is not None else -500
            val_min = val_min if val_min is not None else 500
            if val > val_max:
                val_max = val
            if val < val_min:
                val_min = val

            # Set new vals
            global_vars.instance().set_values({name: val, name + "_max": val_max, name + "_min": val_min})

    while True:
        temp_out, hum_out = dht_out.get_temperature_and_humidity()
        temp_in, hum_in = dht_in.get_temperature_and_humidity()

        #if temp_out is not None and hum_out is not None:
        #    print("Outside Temperature={0:0.1f}F Humidity={1:0.1f}%".format(temp_out, hum_out))
        #if temp_in is not None and hum_in is not None:
        #    print("Inside Temperature={0:0.1f}F Humidity={1:0.1f}%".format(temp_in, hum_in))

        # If it is midnight then reset the mins and maxes so we get fresh values for the new day:
        current_date = date.today()
        if current_date != last_date:
            global_vars.instance().set_values({ \
                "temp_in_min": 500, "temp_in_max": -500, \
                "temp_out_min": 500, "temp_out_max": -500, \
                "hum_in_min": 500, "hum_in_max": -500, \
                "hum_out_min": 500, "hum_out_max": -500, \
                "cpu_temp_min": 500, "cpu_temp_max": -500 \
            })
            last_date = current_date

        # Update the global variables for all the temperatures:
        update_val(temp_in, "temp_in")
        update_val(hum_in, "hum_in")
        update_val(temp_out, "temp_out")
        update_val(hum_out, "hum_out")

        # Set CPU temperature:
        cpu_temp = CPUTemperature().temperature
        update_val(cpu_temp, "cpu_temp")

        time.sleep(2.5)

# Background thread for managing coop door in real-time.
def door_task():
    door = DOOR()
    door_move_count = 0
    DOOR_MOVE_MAX_AFTER_ENDSTOPS = 1
    first_iter = True
    sunrise = None
    door_state = None
    door_override = None
    sunrise = None
    sunset = None
    sentErrorNotification = False

    while True:
        toogle_reference_of_endstops, clearErrorState = global_vars.instance().get_values(["toggle_reference_of_endstops", "clear_error_state"])
    
        generateError = global_vars.instance().get_value("debug_error")
        if generateError:
            door.ErrorState("Test Error")
            global_vars.instance().set_value("debug_error", False)
            
        if clearErrorState:
            door.clear_errorState()
            global_vars.instance().set_value("clear_error_state", False)
            sentErrorNotification = False
        
        if toogle_reference_of_endstops:
            print("Referencing door endstops, waiting for completion.")
            global_vars.instance().set_value("toggle_reference_of_endstops", False)

            if (door.reference_endstops() == False):
                print("Referencing door endstops failed, please check the door and try again.")
                continue
            
            global_vars.instance().set_value("reference_door_endstops_ms", door.reference_door_endstops_ms)
            print("Referencing door endstops complete with total time: " + str(door.reference_door_endstops_ms) + "ms")
        else:
            # Get state and desired state:
            door_state = door.get_state()
            door_override = door.get_override()
            d_door_state, auto_mode, reference_door_endstops_ms = \
                global_vars.instance().get_values(["desired_door_state", "auto_mode","reference_door_endstops_ms"])

            auto_mode = auto_mode == "True"
            #print("Reference door Endstop in MS: " + str(reference_door_endstops_ms))
            # If we are in auto mode then open or close the door based on sunrise
            # or sunset times.
            if auto_mode and not door_override:
                if reference_door_endstops_ms is None:
                    print("Reference door endstops not set. Please run the reference door endstops sequence from the WebUI - disabling auto mode.")
                    global_vars.instance().set_value("auto_mode", "False")
                else:
                    # Get the current sunrise and sunset time, time of close, time of open, and current time.
                    sunrise, sunset = get_sunrise_and_sunset()
                    sunrise_offset, sunset_offset = global_vars.instance().get_values(["sunrise_offset", "sunset_offset"])
                    open_time = sunrise + timedelta(minutes=sunrise_offset)
                    close_time = sunset + timedelta(minutes=sunset_offset)
                    current_time = get_current_time()
                    time_window = timedelta(minutes=1)

                    # If we just booted up, then we need to make sure the door is in the
                    # correct position. This prevents the door from being stuck in the wrong
                    # position due to an unfortunately timed power outage.
                    if first_iter:
                        if current_time >= open_time and current_time < close_time:
                            global_vars.instance().set_value("desired_door_state", "open")
                        else:
                            global_vars.instance().set_value("desired_door_state", "closed")

                    # If we are in the 1 minute after sunrise, command the desired door
                    # state to open.
                    if current_time >= open_time and current_time <= open_time + time_window:
                        global_vars.instance().set_value("desired_door_state", "open")

                    # If we are in the 1 minute after sunset, command the desired door
                    # state to closed.
                    if current_time >= close_time and current_time <= close_time + time_window:
                        global_vars.instance().set_value("desired_door_state", "closed")
                

            # If we are in override mode, then the door is being moved by the switch.
            if door_override:
                ##TODO check how this even works, i cant see that it works at all - atleast not fully controlled by the raspberry pi
                # See if switch is turned off, if so, stop the door.
                door.check_if_switch_neutral()

                # Set the desired state to stopped, so that
                # when override switch is no longer being used,
                # we don't move the motor until a new button is
                # pressed.
                global_vars.instance().set_value("desired_door_state", "stopped")

            # If the door state does not match the desired door state, then
            # we need to move the door.

            
            elif door_state != d_door_state:
                if (door.ErrorState()):
                    door.stop()
                    door_move_count = 0
                    if (sentErrorNotification == False):
                        send_push_notification("Door Error", "The door is in an error state, please check the door.")
                        sentErrorNotification = True
                else:
                    endstopTimeout = door.reference_door_endstops_ms
                    if endstopTimeout is None:
                        endstopTimeout = 10
                        print(f"Reference to endstops not set, the door will move {DOOR_MOVE_MAX_AFTER_ENDSTOPS+endstopTimeout} seconds.")
                    if (door.reference_door_endstops_ms is not None):
                        print(f"Endstop timeout set to: {endstopTimeout} seconds")
                        endstopTimeout = door.reference_door_endstops_ms * 0.001
                    match d_door_state:
                        case "stopped":
                            if door_state in ["open", "closed"]:
                                door.stop(door_state)
                                door_move_count = 0
                            else:
                                door.stop()
                                door_move_count = 0
                        case "open":
                            if door_move_count <= endstopTimeout + DOOR_MOVE_MAX_AFTER_ENDSTOPS:
                                print(f"OPEN: current moveCount: {door_move_count} and endstopTimeout {endstopTimeout} + DOORMOVEMAX {DOOR_MOVE_MAX_AFTER_ENDSTOPS}")
                                door.open()
                                door_move_count += 1
                            else:
                                door.ErrorState("Endstop not reached")
                                global_vars.instance().set_value("desired_door_state", "stopped")
                                door_move_count = 0
                        case "closed":
                            if door_move_count <= endstopTimeout + DOOR_MOVE_MAX_AFTER_ENDSTOPS:
                                print(f"CLOSE: current moveCount: {door_move_count} and endstopTimeout {endstopTimeout} + DOORMOVEMAX {DOOR_MOVE_MAX_AFTER_ENDSTOPS}")
                                door.close()
                                door_move_count += 1
                            else:
                                door.ErrorState("Endstop not reached")
                                global_vars.instance().set_value("desired_door_state", "stopped")
                                door_move_count = 0
                        case _:
                            door.ErrorState("unknown state - i dont know how this could happen")
                            door_move_count = 0
                            assert False, "Unknown state: " + str(d_door_state)

            # We are not in switch override, and the door is in the desired state. The door should be
            # stopped. We can do this most robustly by also checking the switch, which will stop the door
            # as long as it is in the nuetral position. This helps us catch a switch close or open that
            # sometimes gets missed by the edge detection.
            else:
                # Check if switch off, if so, stop the door.
                door.check_if_switch_neutral(nuetral_state=door.get_state())
                door_move_count = 0

            # Set global state
            first_iter = False
            door_state = door.get_state()
            door_override = door.get_override()
            
            global_vars.instance().set_values({ \
                "state": door_state, \
                "override": door_override, \
                "sunrise": sunrise, \
                "sunset": sunset, \
                "error_state": door.errorState if door.ErrorState() else "" \
            })

            time.sleep(1.0)

def data_update_task():
    lastRefreshTime = datetime.now()
    while True:
        #print("Time since last refresh: " + str((datetime.now() - lastRefreshTime).total_seconds() * 1000) + "ms")
        lastRefreshTime = datetime.now()
        try:
            to_send = get_all_data()
            # print(f"Sending data: {to_send}")
            socketio.emit('data', to_send, namespace='/')
        except Exception as e:
            print(f"Error in data update task: {e}")
        time.sleep(1.0)

def data_log_task():
    # Form log file name in form log/YY_MM_DD.csv
    def get_log_file_name():
        current_date = datetime.now()
        formatted_date = current_date.strftime("%Y_%m_%d")
        return os.path.join(os.path.join(root_path, "log"), formatted_date + ".csv")

    # Make log directory:
    log_dir = os.path.dirname(get_log_file_name())
    os.makedirs(log_dir, exist_ok=True)

    last_log_file_name = ""
    while True:
        data = get_all_data()

        # Open new log file and write CSV header if it is a new day
        log_file_name = get_log_file_name()
        if log_file_name != last_log_file_name:
            with open(log_file_name, 'a') as file:
                header = "# " + ", ".join(data.keys()) + "\n"
                file.write(header)

        # Append data to file:
        with open(log_file_name, 'a') as file:
            row = ", ".join(data.values()) + "\n"
            file.write(row)

        # Sleep a bit:
        last_log_file_name = log_file_name
        time.sleep(5.0)

def camera_task():
    if (global_vars.instance().get_value("enable_camera") == False):
        print("Camera is disabled by configuration")
        return
    
    cameraIndex = global_vars.instance().get_value("camera_index")
    print(f"Starting camera task with camera index: {cameraIndex}")
    camera = Camera(device_index=cameraIndex)

    while True:
        try:
            frame = camera.get_frame()
            encoded_frame = base64.b64encode(frame).decode('utf-8')
            socketio.emit('camera', encoded_frame, namespace='/')
        except RuntimeError as e:
            print(f"Error: {e}")
            print("Camera task will end now.")
            break
        time.sleep(0.1)

##################################
# Websocket handlers:
##################################

@socketio.on('connect')
def handle_connect():
    for log_entry in log_buffer:
        socketio.emit('log', {'message': log_entry}, namespace='/')
    # socketio.start_background_task(target=data_update_task)

@socketio.on('disconnect')
def handle_disconnect():
    pass
    #print('Client disconnected')

@socketio.on('open')
def handle_open():
    print('Open button pressed which disables auto mode')
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "open")

@socketio.on('close')
def handle_close():
    print('Close button pressed which disables auto mode')
    
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "closed")

@socketio.on('stop')
def handle_stop():
    print('Stop button pressed which disables auto mode')
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "stopped")

@socketio.on('toggle')
def handle_toggle(message):
    print('Toggle button pressed')
    toggle_value = message['toggle']
    print(f'Toggle button pressed: {toggle_value}')
    if toggle_value:
        print('Auto Mode Enabled')
        global_vars.instance().set_value("auto_mode", "True")
    else:
        print('Auto Mode Disabled')
        global_vars.instance().set_value("auto_mode", "False")

    print(f"current auto mode: {global_vars.instance().get_value('auto_mode')}")
    save_config()

@socketio.on('auto_offsets')
def handle_input_numbers(data):
    sunrise_offset = data['sunrise_offset']
    sunset_offset = data['sunset_offset']
    global_vars.instance().set_values({"sunrise_offset": int(sunrise_offset), "sunset_offset": int(sunset_offset)})
    save_config()

@socketio.on('update_location')
def handle_update_location(location_data):
    # Extract location data from the received message
    city = location_data.get("city")
    region = location_data.get("region")
    timezone = location_data.get("timezone")
    latitude = location_data.get("latitude")
    longitude = location_data.get("longitude")

    # Update global location variables
    new_location = {
        "city": city,
        "region": region,
        "timezone": timezone,
        "latitude": latitude,
        "longitude": longitude
    }
    global_vars.instance().set_value("location", new_location)

    # Save the updated location to the YAML configuration file
    save_config()

    # Reload the sunrise and sunset calculation based on new location
    reload_location_data()

    print(f"Location updated to: {new_location}")

@socketio.on('reference_endstops')
def handle_reference_endstops():
    print('Referencing endstops Socket Command')
    global_vars.instance().set_value("toggle_reference_of_endstops", True)

@socketio.on('clear_error')
def handle_clear_error():
    print('Clearing error state')
    global_vars.instance().set_value("clear_error_state", True)

@socketio.on('generate_error')
def handle_generate_error():
    print('Generating error state')
    global_vars.instance().set_value("debug_error", True)

##################################
# Static page handlers:
##################################

# Route for the home page
@app.route('/')
def index():
    # Render the template with temperature and humidity values
    return render_template(
        'index.html',
        auto_mode=global_vars.instance().get_value("auto_mode"),
        sunrise_offset=global_vars.instance().get_value("sunrise_offset"),
        sunset_offset=global_vars.instance().get_value("sunset_offset"),
        location=global_vars.instance().get_value("location"),
        valid_locations=get_valid_locations(),
        reference_door_endstops_ms=global_vars.instance().get_value("reference_door_endstops_ms"),
        vapid_public_key = global_vars.instance().get_value("vapid_public_key")
    )

@app.template_filter('is_number')
def is_number(value):
    try:
        float(value)
        return True
    except Exception:
        return False
    
@app.route('/manifest.json')
def serve_manifest():
    return send_file('manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    return send_file('sw.js', mimetype='application/javascript')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    subscription = request.json
    print('Received subscription:', subscription)
    currentJsonContent = None
    if os.path.exists(".subscriptions.json"):
        currentJsonContent = json.loads(open(".subscriptions.json").read())

    if currentJsonContent is None:
        currentJsonContent = {"subscriptions": []}

    currentJsonContent["subscriptions"].append(subscription)

    # Save subscription to database (not shown)
    with open('.subscriptions.json', 'w') as f:
        json.dump(currentJsonContent, f)

    return jsonify({'message': 'Subscription successful!'})
    
app.jinja_env.filters['is_number'] = is_number
##################################
# Startup:
##################################

#logger for the webUi Log
class SocketIOHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        log_buffer.append(log_entry)
        socketio.emit('log', {'message': log_entry}, namespace='/')  # Emit new log to clients

def exitHandler(stdout,stderr):
    sys.stdout = stdout
    sys.stderr = stderr
    print("Exiting Coop Controller")

def configure_logging():
    """Configure the logging system."""
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.INFO)

    # Add SocketIO logging handler
    socketio_handler = SocketIOHandler()
    socketio_handler.setFormatter(formatter)
    rootLogger.addHandler(socketio_handler)

    # Add StreamHandler to print logs to the console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    rootLogger.addHandler(console_handler)

    stderrHandler = logging.StreamHandler(sys.stderr)
    stderrHandler.setFormatter(formatter)
    rootLogger.addHandler(stderrHandler)

    return rootLogger

vapid_private_key = None
def send_push_notification(title : str, body : str):
    jsonContent = None
    toRemove = []
    payload = {"title": title, "body": body}
    try:
        print("Sending push notification with payload: " + str(payload))
        global vapid_private_key
        if vapid_private_key is None:
            vapid_private_key = global_vars.instance().get_value("vapid_private_key")
            if vapid_private_key is None:
                print("Vapid private key not set, can't send push notification")
                return

        # Load subscription info from file
        if not os.path.exists(".subscriptions.json"):
            print("No subscriptions file found, can't send push notification")
            return

        jsonContent = json.loads(open(".subscriptions.json").read())
        vapid_claims = {"sub": "mailto:your-email@example.com"}

        for subscription in jsonContent.get("subscriptions", []):
            valid = send_individual_push_notification(subscription, payload, vapid_private_key, vapid_claims)
            if not valid:
                toRemove.append(subscription)
    except Exception as e:
        print(f"Error in send_push_notification: {e}")
    
    for remove in toRemove:
        jsonContent["subscriptions"].remove(remove)

    try:
        with open('.subscriptions.json', 'w') as f:
            json.dump(jsonContent, f)
    except Exception as e:
        print(f"Error in removing invalid subscriptions from file: {e}")
    


def send_individual_push_notification(subscription_info, payload, vapid_private_key, vapid_claims) -> bool:
    try:
        webpush(
            subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            verbose=True,
            timeout=10
        )
    except WebPushException as ex:
        #TODO remove subscription if it is not valid anymore
        print(f"WebPushException occurred: {ex}")
        responseCode = getattr(ex, 'response', None)
        
        if responseCode is not None:
            if responseCode.status_code == 410:
                print("Subscription is no longer valid, removing it.")
                return False
        else:
            print(f"Response: {responseCode}")
            print(f"Status: {getattr(ex, 'status_code', None)}")
    except Exception as ex:
        print(f"General Exception occurred while sending push notification: {ex}")#
    return True


def load_notification_keys():
    secrets_filename = os.path.join(root_path, ".secrets.yaml")
    if os.path.exists(secrets_filename):
        with open(secrets_filename, 'r') as file:
            yaml = YAML.YAML()
            content = file.read()
            yaml_config = yaml.load(content)
            global_vars.instance().set_values(yaml_config["secrets"])
    else:
        print("No secrets file found - the system will missbehave without it.")

if __name__ == '__main__':
    # configure_logging()
    # logging.basicConfig(level=logging.DEBUG)
    print("Starting Coop Controller")

    get_valid_locations()

    load_notification_keys()

    # Initialize the desired door state:
    global_vars.instance().set_value("desired_door_state", "stopped")

    # Load global configuration file into memory
    load_config()

    # Reload location data for sunrise/sunset calculations
    reload_location_data()

    # Start the task that manages the door:
    door_thread = Thread(target=door_task)
    door_thread.daemon = True
    door_thread.start()

    # Start the task that grabs temperature data:
    temp_thread = Thread(target=temperature_task)
    temp_thread.daemon = True
    temp_thread.start()

    # Start the task that logs data to CSV files:
    if (global_vars.instance().get_value("csvLog") == True):
        log_thread = Thread(target=data_log_task)
        log_thread.daemon = True
        log_thread.start()

    #start a broadcast thread for the data
    data_thread = Thread(target=data_update_task)
    data_thread.daemon = True
    data_thread.start()

    # Start the camera task
    camera_thread = Thread(target=camera_task)
    camera_thread.daemon = True
    camera_thread.start()

    # Define the host and port
    host = '0.0.0.0'
    port = 5000

    # Print the IP address and port to the console
    print(f"Starting Flask app on {host}:{port}")

    # Start the Flask app
    socketio.run(app, debug=False, host=host, port=port)
