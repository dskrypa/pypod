"""
Protocols for usbmux
"""

import socket
import struct
import plistlib
from typing import Dict, Union, Optional, Tuple, Any, Mapping

from .exceptions import MuxError, MuxVersionError

__all__ = ['BinaryProtocol', 'PlistProtocol', 'SafeStreamSocket']


class BinaryProtocol:
    TYPE_RESULT = 1
    TYPE_CONNECT = 2
    TYPE_LISTEN = 3
    TYPE_DEVICE_ADD = 4
    TYPE_DEVICE_REMOVE = 5
    VERSION = 0

    def __init__(self, sock):
        self.socket = sock
        self.connected = False

    def _pack(self, req: int, payload: Optional[Mapping[str, Any]]):
        if req == self.TYPE_CONNECT:
            connect_data = b'\x00\x00'
            return struct.pack('IH', payload['DeviceID'], payload['PortNumber']) + connect_data
        elif req == self.TYPE_LISTEN:
            return b''
        else:
            raise ValueError('Invalid outgoing request type %d' % req)

    def _unpack(self, resp: int, payload: bytes) -> Dict[str, Any]:
        if resp == self.TYPE_RESULT:
            return {'Number': struct.unpack('I', payload)[0]}
        elif resp == self.TYPE_DEVICE_ADD:
            devid, usbpid, serial, pad, location = struct.unpack('IH256sHI', payload)
            serial = serial.split(b'\0')[0]
            return {
                'DeviceID': devid,
                'Properties': {
                    'LocationID': location,
                    'SerialNumber': serial,
                    'ProductID': usbpid
                }
            }
        elif resp == self.TYPE_DEVICE_REMOVE:
            devid = struct.unpack('I', payload)[0]
            return {'DeviceID': devid}
        else:
            raise MuxError('Invalid incoming response type %d' % resp)

    def sendpacket(self, req: int, tag: int, payload: Union[Mapping[str, Any], bytes, None] = None):
        payload = self._pack(req, payload or {})
        if self.connected:
            raise MuxError('Mux is connected, cannot issue control packets')
        length = 16 + len(payload)
        data = struct.pack('IIII', length, self.VERSION, req, tag) + payload
        self.socket.send(data)

    def getpacket(self) -> Tuple[int, int, Union[Dict[str, Any], bytes]]:
        if self.connected:
            raise MuxError('Mux is connected, cannot issue control packets')
        dlen = self.socket.recv(4)
        dlen = struct.unpack('I', dlen)[0]
        body = self.socket.recv(dlen - 4)
        version, resp, tag = struct.unpack('III', body[:0xc])
        if version != self.VERSION:
            raise MuxVersionError('Version mismatch: expected %d, got %d' % (self.VERSION, version))
        payload = self._unpack(resp, body[0xc:])
        return resp, tag, payload


class PlistProtocol(BinaryProtocol):
    TYPE_RESULT = 'Result'
    TYPE_CONNECT = 'Connect'
    TYPE_LISTEN = 'Listen'
    TYPE_DEVICE_ADD = 'Attached'
    TYPE_DEVICE_REMOVE = 'Detached'  #???
    TYPE_PLIST = 8
    VERSION = 1

    def _pack(self, req: int, payload: bytes) -> bytes:
        return payload

    def _unpack(self, resp: int, payload: bytes) -> bytes:
        return payload

    def sendpacket(self, req, tag, payload: Optional[Mapping[str, Any]] = None):
        payload = payload or {}
        payload['ClientVersionString'] = 'qt4i-usbmuxd'
        if isinstance(req, int):
            req = [self.TYPE_CONNECT, self.TYPE_LISTEN][req - 2]
        payload['MessageType'] = req
        payload['ProgName'] = 'tcprelay'
        wrapped_payload = plistlib.dumps(payload)
        super().sendpacket(self.TYPE_PLIST, tag, wrapped_payload)

    def getpacket(self):
        resp, tag, payload = super().getpacket()
        if resp != self.TYPE_PLIST:
            raise MuxError('Received non-plist type %d' % resp)
        payload = plistlib.loads(payload)
        return payload.get('MessageType', ''), tag, payload


class SafeStreamSocket:
    def __init__(self, address, family):
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        self.sock.connect(address)

    def send(self, msg):
        totalsent = 0
        while totalsent < len(msg):
            sent = self.sock.send(msg[totalsent:])
            if sent == 0:
                raise MuxError('socket connection broken')
            totalsent = totalsent + sent

    def recv(self, size):
        msg = b''
        while len(msg) < size:
            chunk = self.sock.recv(size - len(msg))
            empty_chunk = b''
            if chunk == empty_chunk:
                raise MuxError('socket connection broken')
            msg = msg + chunk
        return msg
