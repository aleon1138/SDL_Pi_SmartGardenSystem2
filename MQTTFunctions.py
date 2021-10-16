"""
MQTT Functions
"""

import json
import datetime
import paho.mqtt.client as mqttClient
import logger
import state
import config
import pclogging

# pylint: disable=missing-function-docstring
# pylint: disable=unused-argument


def on_WirelessMQTTClientconnect(client, userdata, flags, rc):
    if rc == 0:
        state.WirelessMQTTClientConnected = True
    else:
        logger.log("WirelessMQTTClient Connection failed")


# MQTT Message Types
MQTTTESTMESSAGE = 0
MQTTVALVECHANGE = 1
MQTTALARM = 2
MQTTDEBUG = 3
MQTTSENSORS = 4

# MQTT Publish Message Type
MQTTPUBVALVESET = 10


def on_WirelessMQTTClientmessage(client, userdata, message):
    logger.log(f"Wireless MQTT Message received: {message.payload}")

    MQTTJSON = json.loads(message.payload.decode("utf-8"))

    if str(MQTTJSON["messagetype"]) == str(MQTTVALVECHANGE):
        if config.SWDEBUG:
            logger.log("Valve Change Received")
        pclogging.writeMQTTValveChangeRecord(MQTTJSON)

    if str(MQTTJSON["messagetype"]) == str(MQTTALARM):
        if config.SWDEBUG:
            logger.log("Alarm Message Received")
        pclogging.systemlog(config.CRITICAL, MQTTJSON["argument"])

    if str(MQTTJSON["messagetype"]) == str(MQTTDEBUG):
        if config.SWDEBUG:
            logger.log("Debug Message Received")
        temp = str(MQTTJSON["id"]) + ", " + str(MQTTJSON["value"])
        pclogging.systemlog(config.DEBUG, temp)

    if str(MQTTJSON["messagetype"]) == str(MQTTSENSORS):
        if config.SWDEBUG:
            logger.log("Sensor Message Received")
        processSensorMessage(MQTTJSON)


def processSensorMessage(MQTTJSON):
    if config.SWDEBUG:
        logger.log("-----------------")
        logger.log("Processing MQTT Sensor Message")

    parseSensors = MQTTJSON["sensorValues"]
    parseSensorsArray = parseSensors.split(",")
    for i in range(0, 4):
        for singleSensor in state.moistureSensorStates:
            if singleSensor["id"] == str(MQTTJSON["id"]):
                if singleSensor["sensorNumber"] == str(i + 1):
                    singleSensor["sensorValue"] = str(parseSensorsArray[i])
                    currentTime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    singleSensor["timestamp"] = currentTime

    if config.SWDEBUG:
        logger.log("-----------------")
        logger.log("MoistureSensorStates")
        logger.log(state.moistureSensorStates)

    for singleSensor in state.moistureSensorStates:
        pclogging.sensorlog(
            singleSensor["id"],
            singleSensor["sensorNumber"],
            singleSensor["sensorValue"],
            singleSensor["sensorType"],
            singleSensor["timestamp"],
        )


def on_WirelessMQTTClientlog(client, userdata, level, buf):
    if config.SWDEBUG:
        logger.log(f"MQTT: {buf}")


def startWirelessMQTTClient():
    state.WirelessMQTTClientConnected = False
    state.WirelessMQTTClient = mqttClient.Client("SGS2")
    state.WirelessMQTTClient.on_connect = on_WirelessMQTTClientconnect
    state.WirelessMQTTClient.on_message = on_WirelessMQTTClientmessage
    state.WirelessMQTTClient.on_log = on_WirelessMQTTClientlog
    state.WirelessMQTTClient.connect("127.0.0.1", port=1883)
    state.WirelessMQTTClient.loop_start()


def sendMQTTValve(myID, Valve, State, TimeOn):
    msg = {
        "id": str(myID),
        "messagetype": str(MQTTPUBVALVESET),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "valve": Valve,
        "state": State,
        "timeon": TimeOn,
    }
    state.WirelessMQTTClient.publish(f"SGS/{myID}/Valves", json.dumps(msg))
