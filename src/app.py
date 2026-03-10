#this needs to be at the top of the gevent because the threads somehow interfere - i dont know why but this is the solution for now
from door import DOOR
from door_task_runner import DoorTaskRunner
from gevent import monkey
monkey.patch_all()
from flask import Flask, render_template, Response, send_file, request, jsonify
from threading import Thread, Lock
import threading
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
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
import glob
import sys
from camera import Camera
import base64
from pywebpush import webpush, WebPushException
import atexit

logger = logging.getLogger(__name__)

if os.name != 'nt':
    from gpiozero import CPUTemperature
    import board
    from dht22 import DHT22
    from dht11 import DHT11
else:
    logger.warning("Running on Windows, using mock classes.")
    from MockDHT22 import MockDHT22
    from MockDHT11 import MockDHT11
    from mock_board import MockBoard
    from mock_temperatur import MockCPUTemperature
    CPUTemperature = MockCPUTemperature
    DHT22 = MockDHT22
    DHT11 = MockDHT11
    board = MockBoard()

from location_temperature_sensor import LocationAPITemperatureSensor


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
                "consoleLogToFile": global_vars.instance().get_value("consoleLogToFile"),
                "csvLog": global_vars.instance().get_value("csvLog"),
                "enable_camera" : global_vars.instance().get_value("enable_camera"),
                "camera_index" : global_vars.instance().get_value("camera_index"),
                "outdoor_sensor_type": global_vars.instance().get_value("outdoor_sensor_type")
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
            "consoleLogToFile": False,
            #will be used for currently not implemented stopping/starting of the logging thread
            "csvLog": True,
            "enable_camera" : False,
            "camera_index" : 0,
            # Outdoor sensor backend: "dht22" (physical sensor) or "api" (Open-Meteo)
            "outdoor_sensor_type": "dht22",
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
        logger.info("No configuration file found, creating a new one.")
        save_config()


def get_valid_locations() -> list:
    locations = []
    location_database = database()
    #logger.debug(type(location_database))

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
        cpu_temp_min, cpu_temp_max, reference_door_endstops_ms, auto_mode, error_state, camera_enabled, \
        door_position_estimate \
        = global_vars.instance().get_values(["temp_in", "hum_in", \
            "temp_out", "hum_out", "state", "override", "cpu_temp", \
            "sunrise", "sunset", "sunrise_offset", "sunset_offset", \
            "temp_in_min", "temp_in_max", "hum_in_min", "hum_in_max", \
            "temp_out_min", "temp_out_max", "hum_out_min", "hum_out_max", \
            "cpu_temp_min", "cpu_temp_max", \
            "reference_door_endstops_ms", "auto_mode" , "error_state", "enable_camera", \
            "door_position_estimate"])

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

    def format_temp(temp, units="C", convert_from_f=True):
        if temp is None:
            return ""
        if units == "C" and convert_from_f:
            temp = (temp - 32.0) * (5.0 / 9.0)
        return ("%0.1f" % temp) + u'\N{DEGREE SIGN}' + units

    def format_hum(hum):
        return "%0.1f%%" % hum if hum is not None else ""

    # Return nicely formatted data in dictionary form:
    data_dict = {
      'time': datetime.now().strftime("%H:%M:%S.%f")[:-3],
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
      'cpu_temp': format_temp(cpu_temp, units="C", convert_from_f=False),
      'cpu_temp_min': format_temp(cpu_temp_min, units="C", convert_from_f=False),
      'cpu_temp_max': format_temp(cpu_temp_max, units="C", convert_from_f=False),
      'state': state if state is not None else "",
      'override': state if state is not None and override else "off",
      'door_position_estimate': str(door_position_estimate) if isinstance(door_position_estimate, (int, float)) else str(-1),
      'uptime': str(get_uptime()),
      'sunrise': sunrise.strftime("%I:%M:%S %p").lstrip('0') if sunrise is not None else "",
      'sunset': sunset.strftime("%I:%M:%S %p").lstrip('0') if sunset is not None else "",
      'tu_open': time_until_open_str if time_until_open_str is not None else "",
      'tu_close': time_until_close_str if time_until_close_str is not None else "",
      'reference_door_endstops_ms': str(reference_door_endstops_ms) if reference_door_endstops_ms is not None else "Not set",
      'auto_mode': auto_mode,
      'errorstate' : error_state,
      'camera_enabled' : str(camera_enabled),
      # System metrics
      'cpu_percent': str(round(psutil.cpu_percent(interval=0), 1)),
      'ram_used_mb': str(round(psutil.virtual_memory().used / (1024 * 1024), 0)),
      'ram_total_mb': str(round(psutil.virtual_memory().total / (1024 * 1024), 0)),
      'ram_percent': str(round(psutil.virtual_memory().percent, 1)),
      'disk_used_gb': str(round(psutil.disk_usage('/' if os.name == 'posix' else os.path.splitdrive(os.path.abspath(__file__))[0] + '\\').used / (1024 ** 3), 1)),
      'disk_total_gb': str(round(psutil.disk_usage('/' if os.name == 'posix' else os.path.splitdrive(os.path.abspath(__file__))[0] + '\\').total / (1024 ** 3), 1)),
      'disk_percent': str(round(psutil.disk_usage('/' if os.name == 'posix' else os.path.splitdrive(os.path.abspath(__file__))[0] + '\\').percent, 1)),
      'python_version': sys.version.split()[0],
    }
    return data_dict

##################################
# Background tasks:
##################################

def temperature_task():
    data_pin_in = board.D26          # DHT11 inside sensor on GPIO 26
    dht_in = DHT11(data_pin_in)

    outdoor_sensor_type = global_vars.instance().get_value("outdoor_sensor_type") or "dht22"
    if outdoor_sensor_type == "api":
        logger.info("temperature_task: using LocationAPITemperatureSensor for outdoor readings")
        dht_out = LocationAPITemperatureSensor(
            get_location=lambda: global_vars.instance().get_value("location")
        )
    else:
        logger.info("temperature_task: using DHT22 hardware sensor for outdoor readings")
        data_pin_out = board.D21
        dht_out = DHT22(data_pin_out, power_pin=20)
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
        temp_in, hum_in = dht_in.get_temperature_and_humidity()  # DHT11 inside

        #if temp_out is not None and hum_out is not None:
        #    logger.debug("Outside Temperature={0:0.1f}F Humidity={1:0.1f}%".format(temp_out, hum_out))
        #if temp_in is not None and hum_in is not None:
        #    logger.debug("Inside Temperature={0:0.1f}F Humidity={1:0.1f}%".format(temp_in, hum_in))

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
    """Infinite motor-control loop — thin wrapper around :class:`DoorTaskRunner`.

    All per-iteration logic lives in :meth:`~DoorTaskRunner.step` so it can
    be exercised directly by integration tests without starting an infinite
    loop.  The behaviour is identical to the original monolithic function:
    when :meth:`step` returns ``False`` (aborted reference sequence that
    previously used ``continue``) we loop immediately without sleeping.
    """
    runner = DoorTaskRunner(
        door=DOOR(),
        get_sunrise_sunset=get_sunrise_and_sunset,
        get_current_time=get_current_time,
        send_notification=send_push_notification,
    )

    while True:
        step_completed = runner.step()
        if step_completed:
            time.sleep(runner.thread_sleep_time)

def data_update_task():
    lastRefreshTime = datetime.now()
    while True:
        #logger.debug("Time since last refresh: " + str((datetime.now() - lastRefreshTime).total_seconds() * 1000) + "ms")
        lastRefreshTime = datetime.now()
        try:
            to_send = get_all_data()
            # logger.debug(f"Sending data: {to_send}")
            socketio.emit('data', to_send, namespace='/')
        except Exception as e:
            logger.error(f"Error in data update task: {e}")
        time.sleep(1.0)

# Form log file name in form log/YY_MM_DD.csv
def get_log_file_name():
    current_date = datetime.now()
    formatted_date = current_date.strftime("%Y_%m_%d")
    return os.path.join(os.path.join(root_path, "log"), formatted_date + ".csv")

def data_log_task():
    
    # Make log directory:
    log_dir = os.path.dirname(get_log_file_name())
    os.makedirs(log_dir, exist_ok=True)

    last_log_file_name = ""
    while True:
        data = get_all_data()

        # Open new log file and write CSV header only if the file doesn't exist yet (new day or first run)
        log_file_name = get_log_file_name()
        if log_file_name != last_log_file_name:
            if not os.path.exists(log_file_name):
                with open(log_file_name, 'a') as file:
                    header = "# " + ", ".join(data.keys()) + "\n"
                    file.write(header)

        # Append data to file:
        try:
            with open(log_file_name, 'a') as file:
                row = ", ".join(str(v) for v in data.values()) + "\n"
                file.write(row)
        except Exception as e:
            logger.error(f"data_log_task: failed to write row: {e}")

        # Sleep a bit:
        last_log_file_name = log_file_name
        time.sleep(5.0)

def camera_task():
    if (global_vars.instance().get_value("enable_camera") == False):
        logger.info("Camera is disabled by configuration")
        return
    
    cameraIndex = global_vars.instance().get_value("camera_index")
    logger.debug(f"Starting camera task with camera index: {cameraIndex}")
    camera = Camera(device_index=cameraIndex)

    while True:
        try:
            frame = camera.get_frame()
            encoded_frame = base64.b64encode(frame).decode('utf-8')
            socketio.emit('camera', encoded_frame, namespace='/')
        except RuntimeError as e:
            logger.critical(f"Error: {e}")
            logger.critical("Camera task will end now.")
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
    #logger.debug('Client disconnected')

@socketio.on('open')
def handle_open():
    logger.debug('Open button pressed which disables auto mode')
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "open")

@socketio.on('close')
def handle_close():
    logger.debug('Close button pressed which disables auto mode')
    
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "closed")

@socketio.on('stop')
def handle_stop():
    logger.debug('Stop button pressed which disables auto mode')
    global_vars.instance().set_value("auto_mode", "False")
    global_vars.instance().set_value("desired_door_state", "stopped")

@socketio.on('toggle')
def handle_toggle(message):
    logger.debug('Toggle button pressed')
    toggle_value = message['toggle']
    logger.debug(f'Toggle button pressed: {toggle_value}')
    if toggle_value:
        logger.info('Auto Mode Enabled')
        global_vars.instance().set_value("auto_mode", "True")
    else:
        logger.info('Auto Mode Disabled')
        global_vars.instance().set_value("auto_mode", "False")

    logger.debug(f"current auto mode: {global_vars.instance().get_value('auto_mode')}")
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

    logger.info(f"Location updated to: {new_location}")

@socketio.on('reference_endstops')
def handle_reference_endstops():
    logger.debug('Referencing endstops Socket Command')
    global_vars.instance().set_value("toggle_reference_of_endstops", True)

@socketio.on('clear_error')
def handle_clear_error():
    logger.info('Clearing error state')
    global_vars.instance().set_value("clear_error_state", True)

@socketio.on('generate_error')
def handle_generate_error():
    logger.debug('Generating error state')
    global_vars.instance().set_value("debug_error", True)

@socketio.on('get_csv_data')
def handle_get_csv_data():
    logger.debug('Getting CSV data')
    csv_data = []
    log_file_name = get_log_file_name()
    if os.path.exists(log_file_name):
        with open(log_file_name, 'r') as file:
            csv_data = file.readlines()
        socketio.emit('csv_data', csv_data, namespace='/')   

##################################
# Static page handlers:
##################################

@app.route('/debug')
def debug_panel():
    return render_template('debug.html', is_windows=(os.name == 'nt'))

@app.route('/mock')
def mock_panel():
    if os.name != 'nt':
        return "Mock panel is only available on Windows.", 403
    return render_template('mock.html')

@socketio.on('mock_trigger_pin')
def handle_mock_trigger_pin(data):
    if os.name == 'nt':
        from mock_gpio import GPIO
        pin = data['pin']
        state = GPIO.HIGH if data['state'] == 'HIGH' else GPIO.LOW
        GPIO.trigger_event(pin, state)

@socketio.on('get_debug_data')
def handle_get_debug_data():
    import door as door_module
    # Pin metadata
    pin_meta = {
        17: {"name": "in1", "purpose": "Motor UP", "direction": "OUT"},
        27: {"name": "in2", "purpose": "Motor DOWN", "direction": "OUT"},
        22: {"name": "ena", "purpose": "Motor Enable", "direction": "OUT"},
        23: {"name": "end_up", "purpose": "Endstop UP", "direction": "IN"},
        24: {"name": "end_down", "purpose": "Endstop DOWN", "direction": "IN"},
        5:  {"name": "o_pin", "purpose": "Manual Open Switch", "direction": "IN"},
        6:  {"name": "c_pin", "purpose": "Manual Close Switch", "direction": "IN"},
        21: {"name": "data_pin_out", "purpose": "DHT22 Outdoor Data", "direction": "IN"},
        16: {"name": "data_pin_in", "purpose": "DHT22 Indoor Data", "direction": "IN"},
        20: {"name": "power_pin_out", "purpose": "DHT22 Outdoor Power", "direction": "OUT"},
        26: {"name": "power_pin_in", "purpose": "DHT22 Indoor Power", "direction": "OUT"},
    }

    # Get GPIO pin states
    pins_data = []
    if os.name == 'nt':
        from mock_gpio import GPIO as MockGPIORef
        all_pins = MockGPIORef.get_all_pins()
        for pin_num, meta in sorted(pin_meta.items()):
            pin_info = all_pins.get(pin_num, {})
            pins_data.append({
                "pin": pin_num,
                "name": meta["name"],
                "purpose": meta["purpose"],
                "direction": meta["direction"],
                "state": pin_info.get("state", "N/A"),
                "mode": pin_info.get("mode", "N/A")
            })
    else:
        import RPi.GPIO as RealGPIO
        for pin_num, meta in sorted(pin_meta.items()):
            try:
                state = "HIGH" if RealGPIO.input(pin_num) else "LOW"
            except Exception:
                state = "N/A"
            pins_data.append({
                "pin": pin_num,
                "name": meta["name"],
                "purpose": meta["purpose"],
                "direction": meta["direction"],
                "state": state,
                "mode": meta["direction"]
            })

    # Door module constants
    door_constants = {
        "referenceSequenceTimeout": door_module.referenceSequenceTimeout,
        "invert_end_up": door_module.invert_end_up,
        "invert_end_down": door_module.invert_end_down,
    }

    # All global variables (mask secrets)
    secrets_keys = {"vapid_private_key", "vapid_public_key"}
    all_globals = {}
    raw_globals = global_vars.instance().get_all()
    for k, v in raw_globals.items():
        if k in secrets_keys:
            all_globals[k] = "***" if v else None
        elif isinstance(v, datetime):
            all_globals[k] = v.strftime("%Y-%m-%d %H:%M:%S %Z")
        elif isinstance(v, date):
            all_globals[k] = v.strftime("%Y-%m-%d")
        else:
            all_globals[k] = v

    # System info
    cpu_percent = psutil.cpu_percent(interval=0)
    mem = psutil.virtual_memory()
    system_info = {
        "os_name": os.name,
        "platform": sys.platform,
        "python_version": sys.version,
        "uptime": str(get_uptime()),
        "cpu_percent": cpu_percent,
        "memory_total_mb": round(mem.total / (1024 * 1024), 1),
        "memory_used_mb": round(mem.used / (1024 * 1024), 1),
        "memory_percent": mem.percent,
    }

    # Thread info
    threads_data = []
    for t in threading.enumerate():
        threads_data.append({
            "name": t.name,
            "daemon": t.daemon,
            "alive": t.is_alive()
        })

    # Recent log entries
    recent_logs = list(log_buffer)

    debug_payload = {
        "pins": pins_data,
        "door_constants": door_constants,
        "global_vars": all_globals,
        "system": system_info,
        "threads": threads_data,
        "logs": recent_logs,
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3]
    }
    socketio.emit('debug_data', debug_payload, namespace='/')

@socketio.on('mock_get_outputs')
def handle_mock_get_outputs():
    if os.name == 'nt':
        from mock_gpio import GPIO
        pins = GPIO.get_all_pins()
        outputs = {
            17: pins.get(17, {}).get('state', 'LOW'),
            27: pins.get(27, {}).get('state', 'LOW'),
            22: pins.get(22, {}).get('state', 'LOW')
        }
        socketio.emit('mock_update_outputs', outputs, namespace='/')

# Route for the home page
@app.route('/')
def index():
    # Render the template with temperature and humidity values
    return render_template(
        'grid_dashboard.html',
        auto_mode=global_vars.instance().get_value("auto_mode"),
        sunrise_offset=global_vars.instance().get_value("sunrise_offset"),
        sunset_offset=global_vars.instance().get_value("sunset_offset"),
        location=global_vars.instance().get_value("location"),
        valid_locations=get_valid_locations(),
        reference_door_endstops_ms=global_vars.instance().get_value("reference_door_endstops_ms"),
        vapid_public_key = global_vars.instance().get_value("vapid_public_key"),
        is_windows = os.name == 'nt'
    )

@app.template_filter('is_number')
def is_number(value):
    try:
        float(value)
        return True
    except Exception:
        return False
    
@app.route('/favicon.ico')
def serve_favicon():
    return send_file(
        os.path.join('static', 'favicon_32.png'),
        mimetype='image/png',
    )

@app.route('/manifest.json')
def serve_manifest():
    return send_file('manifest.json', mimetype='application/manifest+json')

@app.route('/sw.js')
def serve_sw():
    return send_file('sw.js', mimetype='application/javascript')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    subscription = request.json
    logger.debug('Received subscription:', subscription)
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

@app.route('/version')
def get_version():
    import subprocess
    try:
        commit_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        commit_hash = 'unknown'
    return jsonify({'version': commit_hash})

# ── Log Viewer API ────────────────────────────────────────────────────────────

@app.after_request
def add_no_cache_headers(response):
    """Prevent browsers and service workers from caching any /api/* response."""
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route('/api/logs')
def api_log_files():
    """Return list of available app log files, newest first."""
    import re as _re
    log_dir = os.path.join(root_path, "log")
    files = []
    if os.path.isdir(log_dir):
        for f in os.listdir(log_dir):
            # Accept: app.log (current day), app.log.YYYY-MM-DD (daily rotations),
            # and legacy app_YYYYMMDD_HHMMSS.log (old boot-timestamped) files.
            is_log = (
                f == 'app.log'
                or (f.startswith('app.log.') and _re.match(r'^\d{4}-\d{2}-\d{2}$', f[8:]))
                or (f.endswith('.log') and f.startswith('app_'))
            )
            if not is_log:
                continue
            full_path = os.path.join(log_dir, f)
            try:
                stat = os.stat(full_path)
                files.append({
                    'name': f,
                    'size': stat.st_size,
                    'modified': stat.st_mtime
                })
            except OSError:
                pass
    # Sort by modification time, newest first
    files.sort(key=lambda x: x['modified'], reverse=True)
    return jsonify(files)


@app.route('/api/logs/<path:filename>')
def api_log_content(filename):
    """Return parsed lines of a single app log file."""
    import re as _re
    # Sanitize: strip any directory traversal
    filename = os.path.basename(filename)
    # Accept: app.log (current day), app.log.YYYY-MM-DD (daily rotations),
    # and legacy app_YYYYMMDD_HHMMSS.log (old boot-timestamped) files.
    _valid_log = (
        filename == 'app.log'
        or (filename.startswith('app.log.') and _re.match(r'^\d{4}-\d{2}-\d{2}$', filename[8:]))
        or (filename.endswith('.log') and filename.startswith('app_'))
    )
    if not _valid_log:
        return jsonify({'error': 'Invalid file type'}), 400

    log_dir = os.path.join(root_path, "log")
    log_path = os.path.join(log_dir, filename)
    if not os.path.isfile(log_path):
        return jsonify({'error': 'File not found'}), 404

    lines = []
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for raw_line in f:
                line = raw_line.rstrip('\n')
                if not line:
                    continue
                # Expected format: TIMESTAMP - LOGGER - LEVEL - MESSAGE
                parts = line.split(' - ', 3)
                if len(parts) == 4:
                    lines.append({
                        't': parts[0],
                        'lg': parts[1],
                        'lv': parts[2].strip().upper(),
                        'm': parts[3]
                    })
                else:
                    lines.append({
                        't': '',
                        'lg': '',
                        'lv': 'RAW',
                        'm': line
                    })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify(lines)

# ── CSV Data Viewer API ───────────────────────────────────────────────────────

@app.route('/api/csv')
def api_csv_files():
    """Return list of available CSV log files, newest first."""
    log_dir = os.path.join(root_path, "log")
    files = []
    if os.path.isdir(log_dir):
        for f in sorted(os.listdir(log_dir), reverse=True):
            if f.endswith('.csv'):
                full_path = os.path.join(log_dir, f)
                try:
                    stat = os.stat(full_path)
                    files.append({
                        'name': f,
                        'size': stat.st_size,
                        'modified': stat.st_mtime
                    })
                except OSError:
                    pass
    return jsonify(files)


@app.route('/api/csv/<path:filename>')
def api_csv_content(filename):
    """Return parsed, downsampled rows from a CSV log file as JSON."""
    import re as _re
    filename = os.path.basename(filename)
    if not filename.endswith('.csv'):
        return jsonify({'error': 'Invalid file type'}), 400

    log_dir = os.path.join(root_path, "log")
    csv_path = os.path.join(log_dir, filename)
    if not os.path.isfile(csv_path):
        return jsonify({'error': 'File not found'}), 404

    NUMERIC_FIELDS = {
        'temp_in', 'temp_in_min', 'temp_in_max',
        'temp_out', 'temp_out_min', 'temp_out_max',
        'hum_in', 'hum_in_min', 'hum_in_max',
        'hum_out', 'hum_out_min', 'hum_out_max',
        'cpu_temp', 'cpu_temp_min', 'cpu_temp_max',
    }
    STRING_FIELDS = {'state', 'override', 'auto_mode', 'errorstate'}
    INCLUDE_FIELDS = NUMERIC_FIELDS | STRING_FIELDS | {'time'}

    try:
        rows = []
        headers = None
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                if headers is None:
                    headers = [h.strip().lstrip('#').strip() for h in line.split(',')]
                    continue
                values = [v.strip() for v in line.split(',')]
                if len(values) < len(headers):
                    continue
                row = {}
                for h, v in zip(headers, values):
                    if h not in INCLUDE_FIELDS:
                        continue
                    if h == 'time':
                        row['time'] = v.split('.')[0].strip()
                    elif h in NUMERIC_FIELDS:
                        numeric = _re.sub(r'[^\d.\-]', '', v)
                        try:
                            row[h] = round(float(numeric), 2)
                        except ValueError:
                            row[h] = None
                    else:
                        row[h] = v
                if 'time' in row:
                    rows.append(row)

        # Downsample to at most 600 points (keeps rendering fast)
        MAX_ROWS = 600
        total = len(rows)
        if total > MAX_ROWS:
            step = total / MAX_ROWS
            rows = [rows[int(i * step)] for i in range(MAX_ROWS)]

        return jsonify({'count': len(rows), 'total': total, 'rows': rows})
    except Exception as e:
        logger.error(f"Error reading CSV file {filename}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/update', methods=['POST'])
def update_app():
    import subprocess
    import sys
    import os
    import time
    from threading import Thread
    
    logger.info("Update requested. Starting update script...")
    
    update_script_path = os.path.join(os.path.dirname(__file__), 'update_script.py')
    app_path = os.path.abspath(__file__)
    
    pid = str(os.getpid())
    service_name = ''
    if os.name != 'nt' and os.environ.get('INVOCATION_ID'):
        try:
            service_name = subprocess.check_output(
                ['systemctl', 'show', '--value', '-p', 'Id', '--pid', pid],
                text=True
            ).strip()  # e.g. 'chicken.service'
        except Exception:
            pass

    cmd = [sys.executable, update_script_path, app_path, pid]
    if service_name:
        cmd.append(service_name)

    if os.name == 'nt':
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen(cmd, preexec_fn=os.setpgrp)
    
    def shutdown():
        time.sleep(1)
        logger.info("Shutting down for update...")
        os._exit(0)
    
    Thread(target=shutdown).start()
    
    response = jsonify({"status": "updating"})
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response
    
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
    logger.info("Exiting Coop Controller")

def configure_logging():
    """Configure the logging system.

    Always writes to a rotating log file in log/ named app_YYYYMMDD_HHMMSS.log
    (timestamp taken at boot).  The file rotates automatically when it reaches
    500 MB (up to 4 backup copies, so 5 files total within one session).  Before
    creating the new file, old base log files are pruned so that at most 5 base
    files (and their rotated copies) exist at any one time.
    """
    logging.basicConfig(level=logging.DEBUG)
    # Define the log format
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Get the root logger
    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.DEBUG)

    # Add SocketIO logging handler
    socketio_handler = SocketIOHandler()
    socketio_handler.setFormatter(formatter)
    if not any(isinstance(h, SocketIOHandler) for h in rootLogger.handlers):
        rootLogger.addHandler(socketio_handler)

    # Add StreamHandler to print logs to the console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    if not any(isinstance(h, logging.StreamHandler) for h in rootLogger.handlers):
        rootLogger.addHandler(console_handler)

    # ------------------------------------------------------------------ #
    # Always-on rotating file handler (boot-timestamped, 500 MB / 5 files)
    # ------------------------------------------------------------------ #
    log_dir = os.path.join(root_path, "log")
    os.makedirs(log_dir, exist_ok=True)

    # Daily-rotating file handler: app.log (today) rolls over at midnight to
    # app.log.YYYY-MM-DD, keeping 30 days of history automatically.
    log_filename = os.path.join(log_dir, "app.log")

    rotating_file_handler = TimedRotatingFileHandler(
        log_filename,
        when='midnight',    # rotate at midnight each day
        interval=1,
        backupCount=30,     # keep 30 days of history
        encoding='utf-8'
    )
    rotating_file_handler.setFormatter(formatter)
    if not any(isinstance(h, (RotatingFileHandler, TimedRotatingFileHandler)) for h in rootLogger.handlers):
        rootLogger.addHandler(rotating_file_handler)

    # Set the log level for the geventwebsocket handler because it is too verbose
    gws_logger = logging.getLogger("geventwebsocket.handler")
    gws_logger.setLevel(logging.WARNING)

    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("location_temperature_sensor").setLevel(logging.INFO)

    return rootLogger


vapid_private_key = None
def send_push_notification(title : str, body : str):
    jsonContent = None
    toRemove = []
    payload = {"title": title, "body": body}
    try:
        logger.debug("Sending push notification with payload: " + str(payload))
        global vapid_private_key
        if vapid_private_key is None:
            vapid_private_key = global_vars.instance().get_value("vapid_private_key")
            if vapid_private_key is None:
                logger.critical("Vapid private key not set, can't send push notification")
                return

        # Load subscription info from file
        if not os.path.exists(".subscriptions.json"):
            logger.critical("No subscriptions file found, can't send push notification")
            return

        jsonContent = json.loads(open(".subscriptions.json").read())
        vapid_claims = {"sub": "mailto:your-email@example.com"}

        for subscription in jsonContent.get("subscriptions", []):
            valid = send_individual_push_notification(subscription, payload, vapid_private_key, vapid_claims)
            if not valid:
                toRemove.append(subscription)
    except Exception as e:
        logger.error(f"Error in send_push_notification: {e}")
    
    for remove in toRemove:
        jsonContent["subscriptions"].remove(remove)

    try:
        with open('.subscriptions.json', 'w') as f:
            json.dump(jsonContent, f)
    except Exception as e:
        logger.debug(f"Error in removing invalid subscriptions from file: {e}")
    


def send_individual_push_notification(subscription_info, payload, vapid_private_key, vapid_claims) -> bool:
    try:
        webpush(
            subscription_info,
            data=json.dumps(payload),
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            timeout=10
        )
    except WebPushException as ex:
        #TODO remove subscription if it is not valid anymore
        logger.debug(f"WebPushException occurred: {ex}")
        responseCode = getattr(ex, 'response', None)
        
        if responseCode is not None:
            if responseCode.status_code == 410:
                logger.debug("Subscription is no longer valid, removing it.")
                return False
        else:
            logger.debug(f"Response: {responseCode}")
            logger.debug(f"Status: {getattr(ex, 'status_code', None)}")
    except Exception as ex:
        logger.error(f"General Exception occurred while sending push notification: {ex}")#
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
        logger.critical("No secrets file found - the system will missbehave without it.")

if __name__ == '__main__':
    
    # Initialize the desired door state:
    global_vars.instance().set_value("desired_door_state", "stopped")
    
    # Load global configuration file into memory
    load_config()

    configure_logging()
    logger.info("Starting Coop Controller")

    get_valid_locations()

    load_notification_keys()



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
    logger.info(f"Starting Flask app on {host}:{port}")

    # Start the Flask app
    socketio.run(app, debug=False, host=host, port=port)
