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
import logger

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
# Update State Lock - keeps sampling from being interrupted (like by checkAndWater) - Locks I2C Access
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
    # Initialize the library (must be called once before other functions).
    strip.begin()
    PixelLock = threading.Lock()


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
        logger.log(f"BMP280 error: {e}")
        logger.log(traceback.format_exc())
    config.BMP280_Present = False

################
# SkyCamera Setup
################


# Detect Camera WeatherSTEMHash
try:

    with picamera.PiCamera() as cam:
        if config.SWDEBUG:
            logger.log(f"Pi Camera Revision {cam.revision}")
        cam.close()
    config.GardenCam_Present = True
except:
    config.GardenCam_Present = False


#############################
# apscheduler setup
#############################
# setup tasks
#############################


def killLogger():
    state.scheduler.shutdown()
    logger.log("Scheduler Shutdown....")
    sys.exit()


def checkAndWater():
    pass


def ap_my_listener(event):
    if event.exception:
        logger.log(event.exception)
        logger.log(event.traceback)


def returnStatusLine(device, state):
    out = f"  [{'x' if state else ' '}] {device}"
    return out


#############################
# initialize Smart Garden System
#############################


def initializeSGSPart1():
    logger.log("###############################################")
    logger.log("SGS2 Version " + SGSVERSION + "  - SwitchDoc Labs")
    logger.log("###############################################")

    if not readJSON.readJSON(""):
        logger.log("no SGS.JSON file present")
        logger.log("configure with 'python3 SGSConfigure.py'")
        sys.exit()

    readJSON.readJSONSGSConfiguration("")

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

    config.Weather_Present = readJSON.getJSONValue("weather")


def initializeSGSPart2():

    # status reports

    logger.log("----------------------")
    logger.log("Local Devices")
    logger.log("----------------------")
    logger.log(returnStatusLine("OLED", config.OLED_Present))
    logger.log(returnStatusLine("BMP280", config.BMP280_Present))
    logger.log(returnStatusLine("DustSensor", config.DustSensor_Present))

    logger.log("----------------------")
    logger.log("Checking Wireless SGS Devices")
    logger.log("----------------------")

    scanForResources.updateDeviceStatus(True)

    # turn off All Valves
    AccessValves.turnOffAllValves()

    wirelessJSON = readJSON.getJSONValue("WirelessDeviceJSON")
    for single in wirelessJSON:
        logger.log(
            returnStatusLine(
                f"{single['name']} - {single['id']}",
                state.deviceStatus[str(single["id"])],
            )
        )

    # Set up Wireless MQTT Links
    MQTTFunctions.startWirelessMQTTClient()

    # subscribe to IDs
    if len(wirelessJSON) == 0:
        logger.log("no Wireless SGS units present")
        logger.log("configure with 'python3 SGSConfigure.py'")
        sys.exit()

    while not state.WirelessMQTTClientConnected:
        time.sleep(0.1)

    for single in wirelessJSON:
        topic = "SGS/" + single["id"]
        logger.log(f"subscribing to {topic}")
        state.WirelessMQTTClient.subscribe(topic)

        # write out to ValveChanges for startup
        myJSON = {
            "id": single["id"],
            "valvestate": "V00000000",
        }
        pclogging.writeMQTTValveChangeRecord(myJSON)

    num_wireless_dev = len(readJSON.getJSONValue("WirelessDeviceJSON"))
    config.moisture_sensor_count = num_wireless_dev * 4
    config.valve_count = num_wireless_dev * 8

    logger.log("----------------------")
    logger.log("Plant / Sensor Counts")
    logger.log("----------------------")
    logger.log(f"Wireless Unit Count: {num_wireless_dev}")
    logger.log(f"Sensor Count: {config.moisture_sensor_count}")
    logger.log(f"Valve Count: {config.valve_count}")
    logger.log("----------------------")
    logger.log("Other Smart Garden System Expansions")
    logger.log("----------------------")
    logger.log(returnStatusLine("Weather", config.Weather_Present))
    logger.log(returnStatusLine("GardenCam", config.GardenCam_Present))
    logger.log(returnStatusLine("SunAirPlus", config.SunAirPlus_Present))
    logger.log(returnStatusLine("SolarMAX", config.SunAirPlus_Present))
    logger.log(returnStatusLine("Lightning Mode", config.Lightning_Mode))
    logger.log(returnStatusLine("MySQL Logging Mode", config.enable_MySQL_Logging))
    logger.log("----------------------")
    sys.stdout.flush()

    # Establish WeatherSTEMHash
    if config.USEWEATHERSTEM:
        state.WeatherSTEMHash = SkyCamera.SkyWeatherKeyGeneration(config.STATIONKEY)


def initializeScheduler():

    state.scheduler.add_listener(ap_my_listener, apscheduler.events.EVENT_JOB_ERROR)

    if config.Weather_Present:
        logger.log("Adding readSensors Job")
        starttime = datetime.datetime.now() + datetime.timedelta(seconds=30)

        state.scheduler.add_job(weatherSensors.readSensors, run_date=starttime)
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

    # MS sensor Read
    AccessMS.initMoistureSensors()
    AccessMS.readAllMoistureSensors()

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
    state.Last_Event = "SGS Started:" + time.strftime("%Y-%m-%d %H:%M:%S")

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


def pauseScheduler():
    jobs = state.scheduler.get_jobs()
    logger.log(f"get_jobs={jobs}")
    for job in jobs:
        state.scheduler.remove_job(job.id)

    jobs = state.scheduler.get_jobs()
    logger.log(f"After get_jobs={jobs}")
    state.scheduler.pause()


def restartSGS():
    state.WirelessMQTTClient.disconnect()
    state.WirelessMQTTClient.loop_stop()
    pauseScheduler()
    initializeSGSPart1()
    initializeSGSPart2()
    initializeScheduler()
    state.scheduler.resume()
    for job in state.scheduler.get_jobs():
        logger.log("job: {job}")
    initializeSGSPart3()


#############################
# main program
#############################

# Main Program
if __name__ == "__main__":

    if config.SWDEBUG:
        logger.log("Starting pigpio daemon")

    # kill all pigpio instances
    try:
        cmd = ["killall", "pigpiod"]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        logger.log(output)
        time.sleep(5)
    except:
        pass

    cmd = ["/usr/bin/pigpiod"]
    output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    logger.log(output)

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
        logger.log("-----------------")
        logger.log("Scheduled Jobs")
        state.scheduler.print_jobs()
        logger.log("-----------------")

        initializeSGSPart3()

        while True:
            if os.path.exists("NEWJSON"):
                os.remove("NEWJSON")
                restartSGS()
                pclogging.systemlog(config.INFO, "Reloading SGS with New JSON")
            time.sleep(10)

    except KeyboardInterrupt:
        logger.log("exiting program")

    finally:
        AccessValves.turnOffAllValves()
        state.WirelessMQTTClient.disconnect()
        state.WirelessMQTTClient.loop_stop()
        logger.log("done")
