#
# TCWMaster
# Set the max current for the slave Tesla Wall Connector(s) based on total network power usage
#
import math
import time
import logging
import logging.handlers
import binascii

# Consts
INCAMPSDELAY = 60           # delay before a twc can increase current
DECAMPSDELAY = 0            # delay before a twc can decrease current
STARTCHARGETIME = 5         # delay 5 seconds after start charging
TIMETOSAVEMODE = 10         # time before going to save mode when no actual total power has been received
TIMETODELTWC = 30           # time before TWC is removed from list when it does not send heartbeats
MSGSLEEP = 0.1              # wait 100ms after sending a message for slave te respond, preventing message collisions
MAXSLAVES = 3               # max number of slaves
TWCMINAMPS = 6.0            # min current needed for charging

# Config paramters
TotalMaxAmps = 25.0         # total max network amps
TWCsTotalMaxAmps = 16.0     # max total current for all wall connectors
TWCMaxAmps = 16.0           # max current per wall connector

#Globals
initialized = False         # has the master been initialized
masterTWCId = 0x8888        # TWC id of this master
masterTWCSign = 0x88        # Sign of this master
twcList = []                # TWC slaves
dataIn = bytearray([])      # data received from serial interface
sendDataCallback = None     # callback function for sendind data to serial interface
otherAmpsHistList = []      # history list with amps in use by other devices
otherAmpsHistMaxCount = 60  # max size of history list ~ 1 minute

# Input vars
scheduledMaxAmps = 99.0     # total max current for all TWCs set by schedule
actualTotalPower = 0.0      # actual total power in use by all devices including TWCs
actualTolalPowerChanged = time.time()
actualVolts = [230]         # actual volts per phase, used for calculating amps - power

# Output vars
totalAmps = 0.0             # total current in use by all devices on one phase
totalChargingAmps = 0.0     # total current for all TWCs used for charging
twcTotalAvailableAmps = 0.0 # current available for all TWCs

# logging
LogLevel = logging.DEBUG
LogFile = "twcmaster.log"



# TWC slave object
class TWC:
    # TWC slave states
    NONE = 0
    CHARGING = 1
    ERROR = 2
    DONOTCHARGE = 3
    READYTOCHARGE = 4
    BUSY = 5
    INCCHARGE = 6
    DECCHARGE = 7
    STARTCHARGING = 8

    # on init set data received from slave linkready msg
    def __init__(self, twcId, maxAmps, version):
        # data received from TWC
        self.twcId = twcId
        self.twcVersion = version
        self.state = TWC.NONE
        self.maxAmps = maxAmps
        self.availableAmps = 0.0
        self.actualAmps = 0.0
        self.lastDataChanged = time.time()
        self.totalKwh = 0
        self.volts = []
        self.lastKwhVoltsRequested = 0
        self.actualPower = 0.0
        self.calculatedWatts = 0.0
        # TWC settings
        self.desiredAmps = 0
        self.setAmps = 0
        self.lastAmpsChanged = 0
        self.startChargingTime = 0

    # set data received from slave heartbeat msg
    def setDataFromTWC(self, state, availAmps, actualAmps):
        if (self.state != state):
            logging.info("TWC(%04x) state changed from %d to %d", self.twcId, self.state, state)
        self.state = state
        self.availableAmps = availAmps
        self.actualAmps = actualAmps
        # calc actual power, use actual volts for calculating powwer, twc volts can be lower
        p = 0.0
        for v in actualVolts:
            p += v * self.actualAmps
        self.actualPower = p
        # calc watts/h
        now = time.time()
        self.calculatedWatts += (now - self.lastDataChanged) * p / 3600.0
        self.lastDataChanged = now

    # set kwh/volts data received from twc
    def setKwhVoltsFromTWC(self, kwh, volts):
        # clear calculatedWatts when kwh changed
        if (kwh != self.totalKwh):
            self.calculatedWatts = 0.0
        self.totalKwh = kwh
        self.volts = volts

    # is this TWC charging or ready to charge
    def isActive(self):
        return (self.state not in {TWC.NONE, TWC.DONOTCHARGE, TWC.READYTOCHARGE}) or (self.actualAmps > 0.5)

    # dead when the slave is not sending heartbeats and charging not stopped
    def isDead(self):
        return (self.lastDataChanged < time.time() - TIMETODELTWC) and (self.setAmps > 0)

    # get the kwh/volts from twc every minute
    def getKwhVoltsMsg(self):
        # only for TWC verion 2 and once per minute
        if ((self.twcVersion == 2) and (self.lastKwhVoltsRequested < time.time() - 60)):
            self.lastKwhVoltsRequested = time.time()
            return bytearray([0xfb, 0xeb, (masterTWCId>>8) & 0xFF, masterTWCId & 0xFF, (self.twcId>>8) & 0xFF, self.twcId & 0xFF, 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00])
        return None

    # get heartbeat msg to send
    def getHeartBeatMsg(self):
        # never use more current TWC or wiring can handle
        self.desiredAmps = math.trunc(min(self.desiredAmps, self.maxAmps, TWCMaxAmps))

        # check if charging was stopped
        if (self.setAmps == 0):
            # start charging when apms >= TWCMINAMPS
            if (self.desiredAmps < TWCMINAMPS):
                # do not start charging
                self.desiredAmps = 0
            else:
                # start charging with 21A in US and 16A in EU for 5 seconds
                if (self.maxAmps >= 80):
                    self.desiredAmps = 21
                else:
                    self.desiredAmps = 16

        # set max amps for TWC when desired is lower or after INCAMPSDELAY sec when current increases
        if (((self.desiredAmps <= self.availableAmps) and (self.lastAmpsChanged < time.time() - DECAMPSDELAY))
                or (self.lastAmpsChanged < time.time() - INCAMPSDELAY)):
            self.setAmps = self.desiredAmps
            if (self.setAmps < TWCMINAMPS):
                # stop charging
                self.setAmps = 0

        # delay 5 seconds after start charging
        if ((self.availableAmps > 0) and (time.time() < self.startChargingTime + STARTCHARGETIME)):
            # don't change charge current setting
            self.setAmps = self.availableAmps

        # start charging?
        if ((self.setAmps > 0) and (self.availableAmps == 0)):
            logging.info("TWC(%04x) START charging %.2f", self.twcId, self.setAmps)
            self.startChargingTime = time.time()

        # stop charging?
        if ((self.setAmps == 0) and (self.availableAmps > 0)):
            logging.info("TWC(%04x) STOP charging", self.twcId)

        if (self.twcVersion == 1):
            return self.createHeartBeatMsg1()
        else:
            return self.createHeartBeatMsg2()

    # Heartbeat message for version 1 twc
    def createHeartBeatMsg1(self):
        # heartbeat msg = FBE0 mastedid slaveid 05 amps*100
        msg = bytearray([0xfb, 0xe0, (masterTWCId>>8) & 0xFF, masterTWCId & 0xFF, (self.twcId>>8) & 0xFF, self.twcId & 0xFF])

        # set new max amps or stop charging when setAmps = 0
        if ((self.availableAmps != self.setAmps) or (self.setAmps == 0)):
            hundredthsOfAmps = int(self.setAmps * 100)
            msg.extend(bytearray([0x05, (hundredthsOfAmps >> 8) & 0xFF, hundredthsOfAmps & 0xFF, 0x00,0x00,0x00,0x00]))
            if (self.availableAmps != self.setAmps):
                self.lastAmpsChanged = time.time()
                logging.info("TWC(%04x) set max amps to: %.2f", self.twcId, self.setAmps)
        else:
            # no change needed
            msg.extend(bytearray([0x00,0x00,0x00,0x00,0x00,0x00,0x00]))

        return msg

    # Heartbeat message for version 2 twc
    def createHeartBeatMsg2(self):
        # twc version 2 can't stop charging, set charge to minimum, and stop communication
        if (self.setAmps == 0):
            if (self.availableAmps > TWCMINAMPS):
                logging.info("TWC(%04x) can not stop charging, set to minimal amps: %.2f and stop communication", self.twcId, TWCMINAMPS)
                self.setAmps = TWCMINAMPS
            else:
                # stop sending heartbeats to stop charging
                self.availableAmps = 0.0
                self.actualAmps = 0.0
                self.actualPower = 0.0
                return None

        # heartbeat msg = FBE0 mastedid slaveid 09/05 amps*100
        msg = bytearray([0xfb, 0xe0, (masterTWCId>>8) & 0xFF, masterTWCId & 0xFF, (self.twcId>>8) & 0xFF, self.twcId & 0xFF])
        if (self.availableAmps != self.setAmps):
            # twc version 2 uses 0x09 for changing current and 0x05 for set charge current (when not charging?)
            cmd = 0x09
            if not self.isActive():
                cmd = 0x05
            # set new max charge amps
            hundredthsOfAmps = int(self.setAmps * 100)
            msg.extend(bytearray([cmd, (hundredthsOfAmps >> 8) & 0xFF, hundredthsOfAmps & 0xFF, 0x00,0x00,0x00,0x00,0x00,0x00]))
            self.lastAmpsChanged = time.time()
            logging.info("TWC(%04x) set max amps to: %.2f", self.twcId, self.setAmps)
        else:
            # no change needed
            msg.extend(bytearray([0x00,0x00,0x00,0x00,0x00,0x00,0x000,0x00,0x00]))

        return msg



# set config parameters
def setConfig(totalmax, twctotal, twc, level, file):
    global TotalMaxAmps
    global TWCsTotalMaxAmps
    global TWCMaxAmps
    global loglevel
    global LogFile
    TotalMaxAmps = totalmax
    TWCsTotalMaxAmps = min(twctotal, TotalMaxAmps)
    TWCMaxAmps = min(twc, TWCsTotalMaxAmps)
    LogLevel = level
    LogFile = file

    # setup logging
    if (len(LogFile) > 0):
        log_handler = logging.handlers.WatchedFileHandler(LogFile)
    else:
        log_handler = logging.StreamHandler()
    log_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s %(message)s'))
    logger = logging.getLogger()
    logger.addHandler(log_handler)
    logger.setLevel(LogLevel)
    logging.info("Set max currents, Total all devices:%.2f Total TWC:%.2f Single TWC:%.2f", totalmax, twctotal, twc)


# Scheduled max current for all TWCs, set in scheduletwc event
def setScheduledMaxAmps(amps):
    global scheduledMaxAmps
    amps = math.trunc(min(amps, TWCsTotalMaxAmps))
    if (amps != scheduledMaxAmps):
        scheduledMaxAmps = amps
        logging.info("ScheduledMaxAmps changed to: %.2f", amps)


# set actuel volts from power supply, used for calculating twc power
def setActualVolts(volts):
    global actualVolts
    actualVolts = volts


# Actual total power in use, set in powerchange event
def setActualTotalPower(power):
    global actualTotalPower
    global actualTolalPowerChanged
    actualTolalPowerChanged = time.time()
    actualTotalPower = power
    # update charging settings
    update()


# set the method callback(bytearray) to call for sending data to TWC slaves over serial interface
def setSendDataCallback(callback = None):
    global sendDataCallback
    sendDataCallback = callback


# reveived data (bytearray) from TWC slaves over serial interface
def dataReceived(data):
    dataIn.extend(data)


# get the total current in use by all devices on one phase
def getTotalAmps():
    return totalAmps


# Get the total charging current
def getTotalChargingAmps():
    return totalChargingAmps


# Get the available current for TWCs
def getTWCTotalAvailableAmps():
    return twcTotalAvailableAmps


# Get actual TWC currents
def getTWCsActualAmps():
    res = {}
    for twc in twcList:
        res[twc.twcId] = twc.actualAmps
    return res


# Get actual TWC amps setting
def getTWCsSetAmps():
    res = {}
    for twc in twcList:
        res[twc.twcId] = twc.setAmps
    return res


# get the calculated charging power per TWC in watts
def getTWCsPower():
    res = {}
    for twc in twcList:
        res[twc.twcId] = twc.actualPower
    return res


# Get total Kwh per TWC
def getTWCsTotalKwh():
    res = {}
    for twc in twcList:
        res[twc.twcId] = twc.totalKwh + twc.calculatedWatts / 1000.0
    return res


# return number of active = charging TWCs
def getActiveTWCs():
    count = 0
    for twc in twcList:
        if (twc.isActive()):
            count += 1
    return count


# calculate the desired current per TWC
#     calculate power used by other devices from total power in use, minus total power in use by twc's
#     asume other devices are on the same phase (to be sure, we don't know)
#     current in use by other devices = (total power - twc power) / volts per phase
#     available for twc's = total max current - other devices current
def calcDesiredAmps():
    global totalAmps
    global totalChargingAmps
    global twcTotalAvailableAmps

    # get Total current and power in use by all wall connectors
    actualTotalTWCsAmps = 0.0
    actualTotalTWCsPower = 0.0
    for twc in twcList:
        actualTotalTWCsAmps +=  twc.actualAmps
        actualTotalTWCsPower += twc.actualPower

    # Total amps per phase in use by all twcs
    totalChargingAmps = actualTotalTWCsAmps

    # FOR TEST WITH FAKE SLAVE !!! TODO:
    global actualTotalPower
    actualTotalPower += actualTotalTWCsPower
    # END FOR TEST WITH FAKE SLAVE

    # power in use by other devices
    actualOtherDevicesPower = max(actualTotalPower - actualTotalTWCsPower, 0)

    # calc current available all TWCs, asume all other devices are on one phase with the lowest voltage = power/volt
    volt = actualVolts[0]
    for v in actualVolts:
        if (v > 0 and v < volt):
            volt = v
    actualOtherDevicesAmps = actualOtherDevicesPower / volt

    # total current in use on one phase
    totalAmps = actualOtherDevicesAmps + totalChargingAmps

    # check if actualTotalPower has been updated the last TIMETOSAVEMODE seconds
    if (actualTolalPowerChanged > time.time() - TIMETOSAVEMODE):
        # use the higest others amps history values for calculating the available amps for twcs
        otherAmpsHistList.append(actualOtherDevicesAmps)
        if len(otherAmpsHistList) > otherAmpsHistMaxCount:
            otherAmpsHistList.pop(0)
        availableForTWCs = min(TotalMaxAmps - max(otherAmpsHistList), TWCsTotalMaxAmps, scheduledMaxAmps)
    else:
        # when no actual current reading is available use save mode setting
        availableForTWCs = min(TWCMINAMPS, TWCsTotalMaxAmps, scheduledMaxAmps)
        logging.error("No actualTotalPower received, use TWCMINAMPS: %d", TWCMINAMPS)

    # get active TWCs eq are charging or ready to charge, use 1 when none
    numOfTWCs = max(getActiveTWCs(), 1)
    # total available for TWCs
    availableForTWCs = max(availableForTWCs, 0)
    if (math.trunc(twcTotalAvailableAmps) != math.trunc(availableForTWCs)):
        logging.info("Actual total: %.2f available: %.2f charging: %.2f", totalAmps, availableForTWCs, totalChargingAmps)
    twcTotalAvailableAmps = availableForTWCs
    # current available per TWC
    availablePerTWC = min(availableForTWCs / numOfTWCs, TWCMaxAmps)
    # TWC current setting
    amps = math.trunc(max(availablePerTWC, 0))
    for twc in twcList:
        twc.desiredAmps = amps

    logging.debug("AvailableForTWCs=%.2f ActiveTWCs=%d Desired amps=%d" , availableForTWCs, numOfTWCs, amps)


# get checksum for message
def calcChecksum(msg, start, end):
    checksum = 0
    for i in range(start, end):
        checksum += msg[i]
    return checksum & 0xFF


# escape msg data
def escapeData(msg):
    data = bytearray([])
    for b in msg:
        if b == 0xc0:
            #escape c0 with dbdc
            data.append(0xdb)
            data.append(0xdc)
        elif b == 0xdb:
            #escape db with dbdd
            data.append(0xdb)
            data.append(0xdd)
        else:
            data.append(b)
    return data


# unescape msg data
def unescapeData(msg):
    data = bytearray([])
    escfound = False
    for b in msg:
        if escfound:
            if b == 0xdc:
                data.append(0xc0)
            elif b == 0xdd:
                data.append(0xdb)
            else:
                data.append(b)
                logging.error("Unknown escape byte sequence 0xdb %02x", b)
            escfound = False
        else:
            if b == 0xdb:
                escfound = True
            else:
                data.append(b)
    return data


# send message to slaves
def sendMsg(msg):
    if msg == None:
        return
    # add checksum
    msgdata = bytearray(msg)
    checksum = calcChecksum(msgdata, 1, len(msgdata))
    msgdata.extend(bytearray([checksum]))
    logging.debug("send:%s", binascii.hexlify(msgdata))
    # escape message data
    msgdata = escapeData(msgdata)
    # add pre and postfix
    data = bytearray([0xc0])
    data.extend(msgdata)
    data.extend(bytearray([0xc0,0xfe]))
    #send to serial interface
    if sendDataCallback:
        sendDataCallback(data)
    else:
        logging.error("sendDataCallback not defined, can not send data out:%s", binascii.hexlify(data))
    # give slave time to respond
    time.sleep(MSGSLEEP)


# get message from slaves, read dataIn and convert to message
def recvMsg():
    # find message between two 0xc0 bytes
    start = dataIn.find(b"\xc0")
    if start < 0:
        return None
    end = dataIn.find(b"\xc0", start + 1)
    if (end-start == 1):
        start = end
        end = dataIn.find(b"\xc0", start + 1)
    if end < 0:
        return None
    # discard data until start
    for i in range(start+1):
        dataIn.pop(0)
    # pop message
    msg = bytearray([])
    for i in range(end-start-1):
        msg.append(dataIn.pop(0))
    # return unescaped data
    return unescapeData(msg)


# handle message reveived from slave
def handleRecvMsg(msg):
    msglen = len(msg)
    if msglen < 14:
        if msglen > 1:
            logging.warn("recv message to short: %s", binascii.hexlify(msg))
        return

    checksum = calcChecksum(msg, 1, msglen - 2)
    msgchecksum = msg[msglen - 1]
    if int(msgchecksum) != checksum:
        logging.warn("recv message with wrong checksum: %s found %02x , expected: %02x", binascii.hexlify(msg), msgchecksum, checksum)
        return

    logging.debug("recv:%s", binascii.hexlify(msg))

    msgtype = (msg[0] << 8) + msg[1]

    if msgtype == 0xfde2:
        # handle linkready from slave
        sender = (msg[2] << 8) + msg[3]
        sign = msg[4]
        amps = ((msg[5] << 8) + msg[6]) / 100.0
        version = 1 if len(msg) == 14 else 2
        logging.info("Linkready from slave: %04x %.2f", sender, amps)
        # if a slave has our id let it change his id
        if sender == masterTWCId:
            logging.info("Slave with same id as master, reinitialize to let slave choose new id")
            initialized = False
            return
        # if twc already in list ignore linkready
        for twc in twcList:
            if twc.twcId == sender:
                logging.debug("TWC(%04x) already in list", sender)
                return
        # create new twc and add it to the list
        newtwc = TWC(sender, amps, version)
        twcList.append(newtwc)
        if len(twcList) > MAXSLAVES:
            twc = twcList.pop(0)
            logging.warn("Exceeded maxium number of slaves, dropped slave %04x", twc.twcId)

    elif msgtype == 0xfde0:
        #handle heartbeat from slave
        sender = (msg[2] << 8) + msg[3]
        receiver = (msg[4] << 8) + msg[5]
        state = msg[6]
        maxamps = ((msg[7] << 8) + msg[8]) / 100.0
        chargeamps = ((msg[9] << 8) + msg[10]) / 100.0
        logging.debug("Heartbeat from slave: slave:%04x master:%04x state:%d set:%.2f cur:%.2f", sender, receiver, state, maxamps, chargeamps)

        if receiver != masterTWCId:
            logging.warn("Heartbeat with unknown master: %04x received from %04x", receiver, sender)
            return
        # update twc data
        for twc in twcList:
            if twc.twcId == sender:
                twc.setDataFromTWC(state, maxamps, chargeamps)
                break
        else:
            logging.error("Unknown TWC Id: %04x", sender)

    elif msgtype == 0xfdeb:
        #handle kwh/volt message from slave
        sender = (msg[2] << 8) + msg[3]
        kwh = ((msg[4] << 24) + (msg[5] << 16) + (msg[6] << 8) + msg[7])
        volts = [(msg[8] << 8) + msg[9], (msg[10] << 8) + msg[11], (msg[12] << 8) + msg[13]]
        logging.debug("Kwh/volts from slave: slave:%04x kwh:%d v1:%d v2:%d v3:%d", sender, kwh, volts[0], volts[1], volts[2])

        # update twc data
        for twc in twcList:
            if twc.twcId == sender:
                twc.setKwhVoltsFromTWC(kwh, volts)
                break
        else:
            logging.error("Kwh/volts message with unknown TWC Id: %04x", sender)

    else:
        logging.warn("Unknown message from slave: %s", binascii.hexlify(msg))


# init Master: send linkready 1 and 2 messages
def initMaster():
    linkready1 = bytearray([0xfc, 0xe1, (masterTWCId>>8) & 0xFF, masterTWCId & 0xFF, masterTWCSign, 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00])
    linkready2 = bytearray([0xfb, 0xe2, (masterTWCId>>8) & 0xFF, masterTWCId & 0xFF, masterTWCSign, 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00])
    for i in range (5):
        sendMsg(linkready1)
    for i in range (5):
        sendMsg(linkready2)


# update TWC's charging setting
def update():
    global initialized

    # init Master
    if not initialized:
        initMaster()
        initialized = True
        return

    # check incomming messages
    msg = recvMsg()
    while msg:
        handleRecvMsg(msg)
        msg = recvMsg()

    # do the calculations and set the TWCs desiredAmps
    calcDesiredAmps()

    # remove twcs that don't send haertbeats to master
    for twc in twcList:
        if twc.isDead():
            twcList.remove(twc)
            logging.warn("No heartbeats receveid from slave, deleted slave %04x", twc.twcId)

    # send heartbeat to slave(s)
    for twc in twcList:
        sendMsg(twc.getHeartBeatMsg())

    # get kwh/Volts from slave(s) (once per minute per twc)
    for twc in twcList:
        sendMsg(twc.getKwhVoltsMsg())


# call this method every second to do the processing
def handleHeartBeat():
    # when update is not triggered by setactualTotalPower do it here
    if (actualTolalPowerChanged < time.time() - 2):
        logging.debug("Update handled by heartbeat")
        update()


#FOR TEST ONLY
# use this only for speedup test scripts!
def speedup4Testing():
    global INCAMPSDELAY
    global DECAMPSDELAY
    global TIMETOSAVEMODE
    global TIMETODELTWC
    global MSGSLEEP
    global otherAmpsHistMaxCount
    global STARTCHARGETIME
    INCAMPSDELAY = 0
    DECAMPSDELAY = 0
    TIMETOSAVEMODE = 5
    TIMETODELTWC = 5
    MSGSLEEP = 0.1
    otherAmpsHistMaxCount = 1
    STARTCHARGETIME = 0
