#!/usr/bin/env python3
"""
Smart Garden System 2
SwitchDoc Labs
"""

# pylint: disable=wrong-import-position
# pylint: disable=missing-function-docstring
# pylint: disable=wrong-import-order

from __future__ import division
from __future__ import print_function
from builtins import range

SGSVERSION = "021"

import sys
import traceback
import os
import RPi.GPIO as GPIO
import time
import threading
import json
import picamera
import subprocess

from bmp280 import BMP280
import SkyCamera
import readJSON
import logging

logging.basicConfig(level=logging.ERROR)

import updateBlynk

sys.path.append("./SDL_Pi_SSD1306")
sys.path.append("./Adafruit_Python_SSD1306")

from neopixel import *
import pixelDriver
from PIL import Image
import Adafruit_SSD1306
import Scroll_SSD1306
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import apscheduler.events
import scanForResources

import config
import pclogging
import state
import Valves
import AccessMS
import AccessValves
import weatherSensors
import wiredSensors

# initialization

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

################
# Update State Lock - keeps smapling from being interrupted (like by checkAndWater) - Locks I2C Access
################
state.UpdateStateLock = threading.Lock()

###############
# Pixel Strip  LED
###############

# Create NeoPixel object with appropriate configuration.
if config.enablePixel:
    strip = Adafruit_NeoPixel(
        pixelDriver.LED_COUNT,
        pixelDriver.LED_PIN,
        pixelDriver.LED_FREQ_HZ,
        pixelDriver.LED_DMA,
        pixelDriver.LED_INVERT,
        pixelDriver.LED_BRIGHTNESS,
        pixelDriver.LED_CHANNEL,
        pixelDriver.LED_STRIP,
    )
    # Intialize the library (must be called once before other functions).
    strip.begin()
    PixelLock = threading.Lock()


###############
# Flash LED
###############


def blinkLED(_, color, times, length):
    if config.enablePixel and state.runLEDs:
        with PixelLock:
            for _ in range(times):
                strip.setPixelColor(0, color)
                strip.show()
                time.sleep(length)
            strip.setPixelColor(0, Color(0, 0, 0))
            strip.show()


################
# SSD 1306 setup
################

# OLED SSD_1306 Detection

try:
    RST = 27
    display = Adafruit_SSD1306.SSD1306_128_64(rst=RST, i2c_address=0x3C)
    display.begin()
    display.clear()
    display.display()
    config.OLED_Present = True
    OLEDLock = threading.Lock()
except:
    config.OLED_Present = False


# import util

###############
# MQTT Setup for Wireless
###############

import MQTTFunctions


################
# BMP280 Setup
################

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus


# Initialise the BMP280
bus = SMBus(1)
bmp280 = BMP280(i2c_dev=bus, i2c_addr=0x77)

try:
    bmp280 = BMP280(i2c_dev=bus, i2c_addr=0x77)
    config.BMP280_Present = True
except Exception as e:
    if config.SWDEBUG:
        print("I/O error({0}): {1}".format(e.errno, e.strerror))
        print(traceback.format_exc())

    config.BMP280_Present = False

################
# SkyCamera Setup
################


# Detect Camera WeatherSTEMHash
try:

    with picamera.PiCamera() as cam:
        if config.SWDEBUG:
            print("Pi Camera Revision", cam.revision)
        cam.close()
    config.GardenCam_Present = True
except:
    config.GardenCam_Present = False


#############################
# apscheduler setup
#############################
# setup tasks
#############################


def tick():
    print(f"The time is: {datetime.datetime.now()}")
    sys.stdout.flush()


def killLogger():
    state.scheduler.shutdown()
    print("Scheduler Shutdown....")
    sys.exit()


def checkAndWater():
    pass


def ap_my_listener(event):
    if event.exception:
        print(event.exception)
        print(event.traceback)


def returnStatusLine(device, state):
    out = f"  [{'X' if state else ' '}] {device}"
    if config.USEBLYNK:
        updateBlynk.blynkTerminalUpdate("Device:" + out)
    return out


def checkForButtons():
    if config.USEBLYNK:
        updateBlynk.blynkStatusUpdate()


def checkForAlarms():
    pass


#############################
# initialize Smart Garden System
#############################


def initializeSGSPart1():
    print("###############################################")
    print("SGS2 Version " + SGSVERSION + "  - SwitchDoc Labs")
    print("###############################################")
    print("")
    print("Program Started at:" + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("")

    # read in JSON
    # read in JSON
    if not readJSON.readJSON(""):
        print("No SGS.JSON file present", file=sys.stderr)
        print("configure with 'sudo python3 SGSConfigure.py'", file=sys.stderr)
        sys.exit()

    readJSON.readJSONSGSConfiguration("")
    # init blynk app state
    if config.USEBLYNK:
        updateBlynk.blynkInit()
    message = "SGS Version " + SGSVERSION + " Started"
    pclogging.systemlog(config.INFO, message)
    pclogging.systemlog(config.JSON, "SGS.JSON Loaded: " + json.dumps(config.JSONData))
    pclogging.systemlog(
        config.JSON,
        "SGSConfigurationJSON.JSON Loaded: " + json.dumps(config.SGSConfigurationJSON),
    )
    pclogging.systemlog(config.CRITICAL, "No Alarm")
    if config.GardenCam_Present:
        pclogging.systemlog(config.INFO, "Garden Cam Present")
    else:
        pclogging.systemlog(config.INFO, "Garden Cam NOT Present")

    # scan and check for resources
    # get if weather is being used
    config.Weather_Present = readJSON.getJSONValue("weather")


def initializeSGSPart2():

    # status reports

    print("----------------------")
    print("Local Devices")
    print("----------------------")
    print(returnStatusLine("OLED", config.OLED_Present))
    print(returnStatusLine("BMP280", config.BMP280_Present))
    print(returnStatusLine("DustSensor", config.DustSensor_Present))

    print("----------------------")
    print("Checking Wireless SGS Devices")
    print("----------------------")

    scanForResources.updateDeviceStatus(True)

    # turn off All Valves
    AccessValves.turnOffAllValves()

    wirelessJSON = readJSON.getJSONValue("WirelessDeviceJSON")
    for single in wirelessJSON:
        print(
            returnStatusLine(
                str(single["name"]) + " - " + str(single["id"]),
                state.deviceStatus[str(single["id"])],
            )
        )

    # Set up Wireless MQTT Links
    MQTTFunctions.startWirelessMQTTClient()

    # subscribe to IDs
    if len(wirelessJSON) == 0:
        print("################################")
        print("ERROR")
        print("################################")
        print("No Wireless SGS uinits present - run SGSConfigure.py")
        print("################################")
        sys.exit()

    while not state.WirelessMQTTClientConnected:
        time.sleep(0.1)

    # subscribe to IDs

    for single in wirelessJSON:
        topic = "SGS/" + single["id"]
        print("subscribing to ", topic)
        state.WirelessMQTTClient.subscribe(topic)
        # write out to ValveChanges for startup
        myJSON = {}
        myJSON["id"] = single["id"]
        myJSON["valvestate"] = "V00000000"
        pclogging.writeMQTTValveChangeRecord(myJSON)

    print()
    print("----------------------")
    print("Plant / Sensor Counts")
    print("----------------------")
    config.moisture_sensor_count = len(readJSON.getJSONValue("WirelessDeviceJSON")) * 4
    config.valve_count = len(readJSON.getJSONValue("WirelessDeviceJSON")) * 8
    print("Wireless Unit Count:", len(readJSON.getJSONValue("WirelessDeviceJSON")))
    print("Sensor Count: ", config.moisture_sensor_count)
    print("Valve Count: ", config.valve_count)
    print()
    if config.USEBLYNK:
        updateBlynk.blynkTerminalUpdate(
            "Wireless Unit Count:%d" % len(readJSON.getJSONValue("WirelessDeviceJSON"))
        )
        updateBlynk.blynkTerminalUpdate(
            "Sensor Count: %d" % config.moisture_sensor_count
        )
        updateBlynk.blynkTerminalUpdate("Pump Count: %d" % config.valve_count)
        updateBlynk.updateStaticBlynk()

    print("----------------------")
    print("Other Smart Garden System Expansions")
    print("----------------------")
    print(returnStatusLine("Weather", config.Weather_Present))
    print(returnStatusLine("GardenCam", config.GardenCam_Present))
    print(returnStatusLine("SunAirPlus", config.SunAirPlus_Present))
    print(returnStatusLine("SolarMAX", config.SunAirPlus_Present))
    print(returnStatusLine("Lightning Mode", config.Lightning_Mode))
    print(returnStatusLine("MySQL Logging Mode", config.enable_MySQL_Logging))
    print(returnStatusLine("UseBlynk", config.USEBLYNK))
    print("----------------------")
    sys.stdout.flush()

    # Establish WeatherSTEMHash
    if config.USEWEATHERSTEM:
        state.WeatherSTEMHash = SkyCamera.SkyWeatherKeyGeneration(config.STATIONKEY)


def initializeScheduler():

    state.scheduler.add_listener(ap_my_listener, apscheduler.events.EVENT_JOB_ERROR)

    # prints out the date and time to console
    state.scheduler.add_job(tick, "interval", seconds=5 * 60)

    # read wireless sensor package
    # print("Before Adding readSensors Job")
    if config.Weather_Present:
        print("Adding readSensors Job")
        # start in 10 seconds
        starttime = datetime.datetime.now() + datetime.timedelta(seconds=30)

        state.scheduler.add_job(
            weatherSensors.readSensors, run_date=starttime
        )  # run in background

        state.scheduler.add_job(
            weatherSensors.writeWeatherRecord, "interval", seconds=15 * 60
        )
        state.scheduler.add_job(
            weatherSensors.writeITWeatherRecord, "interval", seconds=15 * 60
        )

    if config.BMP280_Present:
        wiredSensors.readWiredSensors(bmp280)
        state.scheduler.add_job(
            wiredSensors.readWiredSensors, "interval", args=[bmp280], seconds=500
        )

    # blink optional life light
    state.scheduler.add_job(
        blinkLED, "interval", seconds=5, args=[0, Color(0, 0, 255), 1, 0.250]
    )

    # blink life light
    if config.enablePixel:
        state.scheduler.add_job(
            pixelDriver.statusLEDs, "interval", seconds=15, args=[strip, PixelLock]
        )

    # check device state
    state.scheduler.add_job(
        scanForResources.updateDeviceStatus, "interval", seconds=6 * 120, args=[False]
    )

    # sky camera
    if config.USEWEATHERSTEM:
        if config.GardenCam_Present:
            state.scheduler.add_job(
                SkyCamera.takeSkyPicture,
                "interval",
                seconds=int(config.INTERVAL_CAM_PICS__SECONDS),
            )

    # check for force water - note the interval difference with updateState
    # state.scheduler.add_job(forceWaterPlantCheck, 'interval', seconds=8)

    # every 10 seconds, check for button changes
    state.scheduler.add_job(checkForButtons, "interval", seconds=10)

    # check for alarms
    state.scheduler.add_job(checkForAlarms, "interval", seconds=15)
    # state.scheduler.add_job(checkForAlarms, 'interval', seconds=300)

    # MS sensor Read
    AccessMS.initMoistureSensors()
    AccessMS.readAllMoistureSensors()

    # MQTT now updates the Moisture Sensor arrays

    # state.scheduler.add_job(AccessMS.readAllMoistureSensors, 'interval', minutes=15)

    # sensor timed water and Timed
    tNow = datetime.datetime.now()
    # round to the next full hour
    tNow -= datetime.timedelta(
        minutes=tNow.minute, seconds=tNow.second, microseconds=tNow.microsecond
    )
    state.nextMoistureSensorActivate = tNow

    state.scheduler.add_job(Valves.valveCheck, "interval", minutes=1)

    # sensor manual water
    state.scheduler.add_job(Valves.manualCheck, "interval", seconds=15)

    if config.DustSensor_Present:
        state.scheduler.add_job(DustSensor.read_AQI, "interval", seconds=60 * 11)


def initializeSGSPart3():
    if config.SWDEBUG:
        if config.USEBLYNK:
            print("Blynk Status=", updateBlynk.blynkSGSAppOnline())
            updateBlynk.blynkAlarmUpdate()

    state.Last_Event = "SGS Started:" + time.strftime("%Y-%m-%d %H:%M:%S")

    if config.USEBLYNK:
        updateBlynk.blynkEventUpdate()

    if config.OLED_Present:
        with OLEDLock:
            image = Image.open("SmartPlantPiSquare128x64.ppm").convert("1")
            display.image(image)
            display.display()
            time.sleep(3.0)
            display.clear()
            Scroll_SSD1306.addLineOLED(display, ("    Welcome to "))
            Scroll_SSD1306.addLineOLED(display, ("   Smart Garden "))

    state.Pump_Water_Full = False
    checkAndWater()
    checkForAlarms()


def pauseScheduler():

    state.scheduler.print_jobs()

    jobs = state.scheduler.get_jobs()
    print("get_jobs=", jobs)
    state.scheduler.print_jobs()
    for job in jobs:
        state.scheduler.remove_job(job.id)

    jobs = state.scheduler.get_jobs()
    print("After get_jobs=", jobs)
    state.scheduler.pause()
    print("After get_jobs=", jobs)
    state.scheduler.print_jobs()


def restartSGS():
    state.WirelessMQTTClient.disconnect()
    state.WirelessMQTTClient.loop_stop()
    pauseScheduler()

    initializeSGSPart1()
    initializeSGSPart2()

    initializeScheduler()
    state.scheduler.resume()
    print("After resume=")
    state.scheduler.print_jobs()

    initializeSGSPart3()


#############################
# main program
#############################

# Main Program
if __name__ == "__main__":

    if config.SWDEBUG:
        print("Starting pigpio daemon")

    # kill all pigpio instances
    try:
        cmd = ["killall", "pigpiod"]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        print(output)
        time.sleep(5)
    except:
        pass

    cmd = ["/usr/bin/pigpiod"]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    print(output)
    ################
    # Dust Sensor Setup
    ################
    import DustSensor

    try:
        DustSensor.powerOnDustSensor()
        myData = DustSensor.get_data()
        DustSensor.powerOffDustSensor()
        config.DustSensor_Present = True
    except:
        DustSensor.powerOffDustSensor()
        config.DustSensor_Present = False
    pclogging.readLastHour24AQI()
    initializeSGSPart1()

    try:
        initializeSGSPart2()
        state.scheduler = BackgroundScheduler()

        initializeScheduler()

        state.scheduler.start()
        print("-----------------")
        print("Scheduled Jobs")
        state.scheduler.print_jobs()
        print("-----------------")

        initializeSGSPart3()

        while True:
            if os.path.exists("NEWJSON"):
                print("New JSON files detected, reloading...")
                os.remove("NEWJSON")
                restartSGS()
                pclogging.systemlog(config.INFO, "Reloading SGS with New JSON")
            time.sleep(10)

    except KeyboardInterrupt:
        print("exiting program")

    finally:
        AccessValves.turnOffAllValves()
        state.WirelessMQTTClient.disconnect()
        state.WirelessMQTTClient.loop_stop()
        print("done")
