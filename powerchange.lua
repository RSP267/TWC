-- Lua event for communicating the total current in use by all Devices to TWC plugin
-- The TWC plugin will calculate the current available for charging

-- Power Network
local powerDevice = "Power_Utility"
local voltsDevice1 = "Voltage L1"
local voltsDevice2 = "Voltage L2"
local voltsDevice3 = "Voltage L3"

commandArray = {}

-- get power and volts from P1 smart meter
for deviceName,deviceValue in pairs(devicechanged) do
    if (deviceName == powerDevice) then
        v1 = otherdevices[voltsDevice1]
        v2 = otherdevices[voltsDevice2]
        v3 = otherdevices[voltsDevice3]
        commandArray['TWC - Network current'] = tostring(deviceValue)..";"..tostring(v1 or 0)..";"..tostring(v2 or 0)..";"..tostring(v3 or 0)
    end
end

return commandArray
