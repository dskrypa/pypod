"""
USBMux client that handles iDevice descovery via USB.

:author: Doug Skrypa (original: Hector Martin "marcan" <hector@marcansoft.com>)
"""

import socket
import select
import sys
import plistlib
from typing import Optional, List

from .exceptions import MuxError, MuxVersionError, NoMuxDeviceFound
from .protocol import BinaryProtocol, PlistProtocol, SafeStreamSocket

__all__ = ['USBMux', 'MuxConnection', 'MuxDevice', 'UsbmuxdClient']


class MuxDevice:
    def __init__(self, devid, usbprod, serial, location, proto_cls, socket_path):
        self.devid = devid
        self.usbprod = usbprod
        self.serial = serial
        self.location = location
        self._proto_cls = proto_cls
        self._socket_path = socket_path

    def __repr__(self):
        fmt = '<MuxDevice: ID %d ProdID 0x%04x Serial %r Location 0x%x>'
        return fmt % (self.devid, self.usbprod, self.serial, self.location)

    def connect(self, port):
        connector = MuxConnection(self._socket_path, self._proto_cls)
        return connector.connect(self, port)


class MuxConnection:
    def __init__(self, socketpath, protoclass):
        self.socketpath = socketpath
        if sys.platform in ('win32', 'cygwin'):
            family = socket.AF_INET
            address = ('127.0.0.1', 27015)
        else:
            family = socket.AF_UNIX
            address = self.socketpath
        self.socket = SafeStreamSocket(address, family)
        self.proto = protoclass(self.socket)
        self.pkttag = 1
        self.devices = []  # type: List[MuxDevice]

    def _getreply(self):
        while True:
            resp, tag, data = self.proto.getpacket()
            if resp == self.proto.TYPE_RESULT:
                return tag, data
            else:
                raise MuxError('Invalid packet type received: %d' % resp)

    def _processpacket(self):
        resp, tag, data = self.proto.getpacket()
        if resp == self.proto.TYPE_DEVICE_ADD:
            self.devices.append(
                MuxDevice(
                    data['DeviceID'],
                    data['Properties']['ProductID'],
                    data['Properties']['SerialNumber'],
                    data['Properties']['LocationID'],
                    self.proto.__class__,
                    self.socketpath
                )
            )
        elif resp == self.proto.TYPE_DEVICE_REMOVE:
            for dev in self.devices:
                if dev.devid == data['DeviceID']:
                    self.devices.remove(dev)
        elif resp == self.proto.TYPE_RESULT:
            raise MuxError('Unexpected result: %d' % resp)
        else:
            raise MuxError('Invalid packet type received: %d' % resp)

    def _exchange(self, req, payload=None):
        mytag = self.pkttag
        self.pkttag += 1
        self.proto.sendpacket(req, mytag, payload or {})
        recvtag, data = self._getreply()
        if recvtag != mytag:
            raise MuxError('Reply tag mismatch: expected %d, got %d' % (mytag, recvtag))
        return data['Number']

    def listen(self):
        ret = self._exchange(self.proto.TYPE_LISTEN)
        if ret != 0:
            raise MuxError('Listen failed: error %d' % ret)

    def process(self, timeout: Optional[float] = None):
        if self.proto.connected:
            raise MuxError('Socket is connected, cannot process listener events')
        rlo, wlo, xlo = select.select([self.socket.sock], [], [self.socket.sock], timeout)
        if xlo:
            self.socket.sock.close()
            raise MuxError('Exception in listener socket')
        if rlo:
            self._processpacket()

    def connect(self, device, port) -> socket.socket:
        ret = self._exchange(
            self.proto.TYPE_CONNECT, {'DeviceID': device.devid, 'PortNumber': ((port << 8) & 0xFF00) | (port >> 8)}
        )
        if ret != 0:
            raise MuxError('Connect failed: error %d' % ret)
        self.proto.connected = True
        return self.socket.sock

    def close(self):
        self.socket.sock.close()


class USBMux:
    def __init__(self, socket_path=None):
        socket_path = socket_path or '/var/run/usbmuxd'
        self.socketpath = socket_path
        self.listener = MuxConnection(socket_path, BinaryProtocol)
        try:
            self.listener.listen()
            self.version = 0
            self.protoclass = BinaryProtocol
        except MuxVersionError:
            self.listener = MuxConnection(socket_path, PlistProtocol)
            self.listener.listen()
            self.protoclass = PlistProtocol
            self.version = 1
        self.devices = self.listener.devices  # type: List[MuxDevice]

    def process(self, timeout: float = 0.1):
        self.listener.process(timeout)

    def find_device(self, serial=None, timeout=0.1, max_attempts=5) -> MuxDevice:
        attempts = 0
        while not self.devices and attempts < max_attempts:
            self.process(timeout)
            attempts += 1

        if devices := self.devices:
            if serial:
                for device in devices:
                    if device.serial == serial:
                        return device
                raise NoMuxDeviceFound(f'Found {len(devices)} MuxDevice instances, but none with {serial=!r}')
            else:
                return devices[0]

        raise NoMuxDeviceFound('No MuxDevice instances were found')


class UsbmuxdClient(MuxConnection):
    def __init__(self):
        super().__init__('/var/run/usbmuxd', PlistProtocol)

    def get_pair_record(self, udid):
        tag = self.pkttag
        self.pkttag += 1
        payload = {'PairRecordID': udid}
        self.proto.sendpacket('ReadPairRecord', tag, payload)
        _, recvtag, data = self.proto.getpacket()
        if recvtag != tag:
            raise MuxError('Reply tag mismatch: expected %d, got %d' % (tag, recvtag))
        pair_record = data['PairRecordData']
        pair_record = plistlib.loads(pair_record)
        return pair_record
