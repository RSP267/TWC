
"""
<plugin key="TWC" name="Tesla Wall Connector Plugin" author="RSP267" version="1.0.0" externallink="https://github.com/RSP267/TWC">
    <params>
        <param field="SerialPort" label="Serial Port" width="300px" required="true" default=""/>
        <param field="Mode1" label="Max network current" width="50px" default="25"/>
        <param field="Mode2" label="Max current all TWC's" width="50px" default="16"/>
        <param field="Mode3" label="Max current per TWC" width="50px" default="16"/>
        <param field="Mode4" label="Log Level" width="150px">
            <options>
                <option label="Debug" value="Debug"/>
                <option label="Normal" value="Normal"  default="true" />
            </options>
        </param>
        <param field="Mode5" label="Log File" width="300px" default="/var/log/twcmaster.log"/>
    </params>
</plugin>
"""

import Domoticz
import twcmaster
import logging
import binascii

# RS485 connection
SerialConn = None
loglevel = logging.INFO

# start plugin: set config, devices en connect serial connection
def onStart():
    global loglevel

    Domoticz.Log("Start TWC plugin")

    # set twcmaster config
    loglevel = logging.INFO
    if Parameters["Mode4"] == "Debug":
        loglevel = logging.DEBUG
    logfile = Parameters["Mode5"]
    twcmaster.setConfig(float(Parameters["Mode1"]), float(Parameters["Mode2"]), float(Parameters["Mode3"]), loglevel, logfile)

    # set sendData method
    twcmaster.setSendDataCallback(sendData)

    # add devices
    if (1 not in Devices):
        Domoticz.Device(Name="Network current", Unit=1, TypeName="Current (Single)").Create()
    if (2 not in Devices):
        Domoticz.Device(Name="Total charge", Unit=2, TypeName="Current (Single)").Create()
    if (3 not in Devices):
        Domoticz.Device(Name="Total available", Unit=3, TypeName="Current (Single)").Create()
    if (4 not in Devices):
        Domoticz.Device(Name="Charge", Unit=4, TypeName="Current/Ampere").Create()
    if (5 not in Devices):
        Domoticz.Device(Name="Setting", Unit=5, TypeName="Current/Ampere").Create()
    if (11 not in Devices):
        Domoticz.Device(Name="1 Power", Unit=11, TypeName="kWh").Create()
    if (12 not in Devices):
        Domoticz.Device(Name="2 Power", Unit=12, TypeName="kWh").Create()
    if (13 not in Devices):
        Domoticz.Device(Name="3 Power", Unit=13, TypeName="kWh").Create()

    # connect to rs485 port
    SerialConn = Domoticz.Connection(Name="TWC", Transport="Serial", Address=Parameters["SerialPort"], Baud=9600)
    SerialConn.Connect()

    # check every second
    Domoticz.Heartbeat(1)
    DumpConfigToLog()

# stop plugin
def onStop():
    Domoticz.Log("onStop called")

# connected?
def onConnect(Connection, Status, Description):
    global SerialConn
    if (Status == 0):
        Domoticz.Log("Connected successfully to: "+Parameters["SerialPort"])
        SerialConn = Connection
    else:
        Domoticz.Log("Failed to connect ("+str(Status)+") to: "+Parameters["SerialPort"]+" with error: "+Description)
    return True

# message received from twc
def onMessage(Connection, Data):
    msg = bytearray([])
    for b in range(len(Data)):
        msg.append(Data[b])
    if (loglevel == logging.DEBUG):
        Domoticz.Log("onMessage: " + str(binascii.hexlify(msg)))
    twcmaster.dataReceived(msg)

# commend from domitics
# device "TWC - Network current" -> set total current in use and the voltage(s)
# device "TWC - Total charge" -> set scheduled max current
def onCommand(Unit, Command, Level, Hue):
    Domoticz.Log("onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))
    if (Unit == 1):
        # command = amps;v1;v2;v3
        values = Command.split(";")
        power = float(values.pop(0))
        volts = []
        for val in values:
            volts.append(float(val))

        # set actual volts first, setting total amps will trigger the calculations
        twcmaster.setActualVolts(volts)
        twcmaster.setActualTotalPower(power)
        if (1 in Devices):
            Devices[1].Update(nValue=0, sValue=str(round(twcmaster.getTotalAmps(), 2)))
    if (Unit == 2):
        # set max charge current
        amps = float(Command)
        twcmaster.setScheduledMaxAmps(amps)

# plugin notification
def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    Domoticz.Log("Notification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(Priority) + "," + Sound + "," + ImageFile)

# connection disconnected
def onDisconnect(Connection):
    Domoticz.Log("onDisconnect called")

# heartbeat: do the processing
def onHeartbeat():
    # call twcmaster heartbeat
    twcmaster.handleHeartBeat()

    # get twc power
    twcpow = twcmaster.getTWCsPower()
    twckwh = twcmaster.getTWCsTotalKwh()
    twcPowerValues = []
    for twcid, power in sorted(twcpow.items()):
        kwh = twckwh[twcid]
        twcPowerValues.append(str(round(power, 0)) + ";" + str(round(kwh * 1000, 0)))
    for i in range(len(twcPowerValues), 3):
        twcPowerValues.append("null;null")

    # update devices
    if (2 in Devices):
        Devices[2].Update(nValue=0, sValue=str(round(twcmaster.getTotalChargingAmps(), 2)))
    if (3 in Devices):
        Devices[3].Update(nValue=0, sValue=str(round(twcmaster.getTWCTotalAvailableAmps(), 2)))
    if (4 in Devices):
        setDeviceValues(Devices[4], twcmaster.getTWCsActualAmps().items(), 3, 2)
    if (5 in Devices):
        setDeviceValues(Devices[5], twcmaster.getTWCsSetAmps().items(), 3, 2)
    if (11 in Devices):
        Devices[11].Update(nValue=0, sValue=twcPowerValues[0])
    if (12 in Devices):
        Devices[12].Update(nValue=0, sValue=twcPowerValues[1])
    if (13 in Devices):
        Devices[13].Update(nValue=0, sValue=twcPowerValues[2])


'''
Generic helper functions
'''

# send message to slave TWC(s)
def sendData(data):
    if (SerialConn):
        SerialConn.Send(data)
        if (loglevel == logging.DEBUG):
            Domoticz.Log("Send:" + str(binascii.hexlify(data)))

# set current device values
def setDeviceValues(device, values, count, decimals):
    s = ""
    for k, v in sorted(values):
        s = s + str(round(v, decimals)) + ";"
    for i in range(len(values), count):
        s = s + "null;"
    s = s.rstrip(';')
    device.Update(nValue=0, sValue=s)

# dump config
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Log( "'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Log("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Log("Device:           " + str(x) + " - " + str(Devices[x]) + " "+ str(Devices[x].LastUpdate))
    return
