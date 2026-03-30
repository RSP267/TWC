# Domoticz TWC plugin
Domoticz plugin for the Tesla Wall Connector


### Install pi os (lite):
https://www.raspberrypi.org/software


### update Python:
sudo apt-get install python3.7 libpython3.7 python3.7-dev -y


### disable swap file:
sudo dphys-swapfile swapoff
sudo dphys-swapfile uninstall
sudo update-rc.d dphys-swapfile remove
sudo systemctl disable dphys-swapfile.service


### Install Domoticz:
https://www.domoticz.com/wiki/Raspberry_Pi

curl -L https://install.domoticz.com | bash


### Add P1 meter hardware:
https://www.robbshop.nl/blog/domoticz-slimme-meter-kabel-installeren



### Persist USB devices:
https://www.domoticz.com/wiki/PersistentUSBDevices

sudo nano /etc/udev/rules.d/99-usb-serial.rules
paste:
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", SYMLINK+="ttyUSB-P1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="ttyUSB-TWC"

sudo shutdown -r now


### Install TWC plugin:

cd ~/domoticz/plugins
git clone https://github.com/RSP267/TWC.git TWC
sudo systemctl restart domoticz.service

### Domoticz:Setup-Hardware
Add P1 smart meter USB
Name: P1
Serial Port: ttyUSB-P1
Baudrate: 115200

### Add Tesla Wall Connector Plugin
Name: TWC
Serial Port: ttyUSB-TWC

### Domoticz:Setup-Devices
Add all devices from P1 and TWC

### Domoticz:Setup-More options-events
Add Lua event all devices
Paste powerchange.lua file content


### Logrotate:
sudo nano /etc/logrotate.d/twcmaster

paste:
/var/log/twcmaster.log {
daily
copytruncate
rotate 5
compress
delaycompress
notifempty
nomail
}
