## Title:       send.py
## Author:      Jeroen Venema
## Created:     25/10/2022
## Last update: 28/10/2025
##
## Edited by Steve Lovejoy for linux DTR issue. 
##
## syntax
## send.py FILENAME <PORT> <BAUDRATE>
## 

## Modinfo:
## 25/10/2022 initial version
## 10/09/2023 Script converts binary file to Intel Hex during transmission. 
##            Using defaults as constants.
## 11/09/2023 Wait time variable introduced for handling PC serial drivers with low buffer memory
## 29/01/2024 OS-specific serial settings to prevent board reset upon opening of serial port
## 22/10/2025 Removed termios, several issues with MacOS. Tested correct DTR on Win/Linux/MacOS
## 28/10/2025 Improved autodetection of usb/serial interfaces

DEFAULT_START_ADDRESS = 0x40000
DEFAULT_BAUDRATE      = 115200
DEFAULT_LINE_WAITTIME = 0.000      ## A value of +/- 0.003 Helps PC serial drivers with low buffer memory
IHEXBYTELENGTH = 255
WAITCRC = True  # request extended format, if available from the VDP

def hexchecksum(hexstring):
    bytestring = b"" # Just to shut up the lexical analyzer
    result = hexstring[1:]    
    checksum = 0
    msn = True
    for digit in result:
        if msn:
            bytestring = digit
            msn = False
        else:
            bytestring += digit 
            checksum += int(bytestring, 16)
            msn = True

    checksum = checksum & 0xff
    checksum = (~checksum & 0xff) + 1
    return checksum

def create_startrecord(crc32):
    startrecord = ':060000FF0000' + hex(crc32)[2:].zfill(8).upper()
    startrecord += hex(hexchecksum(startrecord))[2:].zfill(2).upper()
    return startrecord

def create_stoprecord():
    stoprecord = ':020000FF0001'
    stoprecord += hex(hexchecksum(stoprecord))[2:].zfill(2).upper()
    return stoprecord

import sys
import time
import os
import os.path
import tempfile

try:
  import serial
  import serial.tools.list_ports
except ModuleNotFoundError:
  sys.exit('Error: missing \'pyserial\' module')
try:
  from intelhex import IntelHex
except ModuleNotFoundError:
  sys.exit('Error: missing \'intelhex\' module')
try:
  import crcmod
except ModuleNotFoundError:
  sys.exit('Error: missing \'crcmod\' module')

crc16 = crcmod.mkCrcFun(0x18005, 0x0, False, 0x0)
crc32 = crcmod.crcmod.predefined.Crc('crc-32')

if len(sys.argv) == 1 or len(sys.argv) >4:
  sys.exit('Usage: send.py FILENAME <PORT> <BAUDRATE>')

if not os.path.isfile(sys.argv[1]):
  sys.exit(f'Error: file \'{sys.argv[1]}\' not found')

if len(sys.argv) == 2:
  serialports = serial.tools.list_ports.comports()
  # Filter out common non-usb serial devices from the autodetect list
  ports = []
  for port in serialports:
    ports.append(str(port))
  serialports = list(filter(lambda x: "usb" in x.lower(), ports))
  # Error out if autodetection still results in multiple ports
  if len(serialports) > 1:
    print("Multiple serial ports present - cannot automatically select:")
    for serialport in serialports:
      print(serialport)
    sys.exit()
  if len(serialports) == 0:
    sys.exit("No usb serial port found")
  serialport = str(serialports[0]).split(" ")[0]
if len(sys.argv) >= 3:
  serialport = sys.argv[2]

if len(sys.argv) == 4:
  baudrate = int(sys.argv[3])
else:
  baudrate = DEFAULT_BAUDRATE

nativehexfile = ((sys.argv[1])[-3:] == 'hex') or ((sys.argv[1])[-4:] == 'ihex')

# report parameters used
print(f'Sending \'{sys.argv[1]}\' ', end="")
if nativehexfile: print('as native hex file')
else: 
  print('as binary data, in Intel Hex format')
  print(f'Using start address 0x{DEFAULT_START_ADDRESS:x}')
print(f'Using serial port {serialport}')
print(f'Using Baudrate {baudrate}')

if nativehexfile:
  # calculate crc32 of actual hex content data
  ihex = IntelHex()
  ihex.loadhex(sys.argv[1])
  for start,end in ihex.segments():
     while start != end:
        byte = ihex[start].to_bytes(1,"little")
        crc32.update(byte)
        start += 1
  
  # open native hex file for sending
  file = open(sys.argv[1], "r")
  content = file.readlines()
else:
  # Instantiate ihex object and load binary file to it, write out as ihex format to temp file
  ihex = IntelHex()
  file = tempfile.TemporaryFile("w+t")
  ihex.loadbin(sys.argv[1], offset=DEFAULT_START_ADDRESS)
  ihex.write_hex_file(file, byte_count = IHEXBYTELENGTH)
  file.seek(0)
  # Calculate crc from binary file
  with open(sys.argv[1], "rb") as f:
    byte = f.read(1)
    while byte:
      crc32.update(byte)
      byte = f.read(1)

ser = serial.Serial()
ser.baudrate = baudrate
ser.port = serialport
ser.timeout = 2

# OS-specific serial dtr/rts settings
if(os.name == 'nt'):
  ser.setDTR(False)
  ser.setRTS(False)
if(os.name == 'posix'):
  ser.rtscts = False        # not setting to false prevents communication
  ser.dsrdtr = False        # determines if Agon resets or not

try:
    ser.open()
    print('Opening serial port...')

    if not nativehexfile:
      content = file

    if WAITCRC:
      startrecord = create_startrecord(crc32.crcValue)
      ser.write(startrecord.encode())
      sent = crc16(startrecord.strip().encode('ascii'))
      ser.write(hex(sent)[2:].zfill(4).upper().encode())

      time.sleep(0.5)
      if ser.in_waiting:
        ret = int.from_bytes(ser.read(2), "little")
        sent = crc16(startrecord.encode('ascii'))         
        if ret != sent:
          sys.exit("Extended header sending error, aborting")
      else:
        WAITCRC = False # Target VDP has no extended code built in
        print('VDP doesn\'t support extended CRC16/32; sending regular format')

    print('Sending data...')
    for line in content:
        linesent_ok = False
        while not linesent_ok:
            ser.write(str(line).strip().encode('ascii'))
            linesent_ok = True

            if WAITCRC:
              sent = crc16(line.strip().encode('ascii'))
              ser.write(hex(sent)[2:].zfill(4).upper().encode())
              while ser.in_waiting < 2:
                  pass
              ret = int.from_bytes(ser.read(2), "little")
              if ret != sent:
                  print("CRC error, retransmitting")
                  linesent_ok = False
            time.sleep(DEFAULT_LINE_WAITTIME)

    if WAITCRC:
        while ser.in_waiting == 0:
           pass
        crc32result = int.from_bytes(ser.read(4), "little")
        if crc32result == crc32.crcValue:
            print('CRC - OK')
        else:
            print('CRC ERROR')
    print('Done')

    ser.close()
except serial.SerialException:
    sys.exit('Error: serial port unavailable')

file.close()

