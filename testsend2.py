import gatt
import gpsd
import struct
import time

# Connect to GPS
gpsd.connect()

# Bluetooth adapter
adapter = gatt.DeviceManager(adapter_name='hci0')

class RobotPeripheral(gatt.Device):
    def __init__(self, mac_address, manager):
        super().__init__(mac_address=mac_address, manager=manager)

class GPSService(gatt.Service):
    UUID = '12345678-1234-5678-1234-56789abcdef0'

class GPSCharacteristic(gatt.Characteristic):
    UUID = 'abcdef01-1234-5678-1234-56789abcdef0'

    def __init__(self, service):
        super().__init__(service, self.UUID, ['read', 'notify'])

    def ReadValue(self, options):
        packet = gpsd.get_current()
        lat = packet.lat
        lon = packet.lon
        # Pack as 8 bytes (float32)
        data = struct.pack('ff', lat, lon)
        return [c for c in data]

    def StartNotify(self):
        print("Client subscribed to GPS updates")
        self.notify_loop()

    def notify_loop(self):
        packet = gpsd.get_current()
        lat = packet.lat
        lon = packet.lon
        data = struct.pack('ff', lat, lon)
        self.PropertiesChanged({"Value": [c for c in data]}, [])
        # Notify again after 1 sec
        adapter.run_loop.call_later(1.0, self.notify_loop)

# Initialize device (peripheral)
robot = RobotPeripheral(mac_address=None, manager=adapter)

# Add service & characteristic
gps_service = GPSService(robot)
gps_char = GPSCharacteristic(gps_service)

adapter.run()
