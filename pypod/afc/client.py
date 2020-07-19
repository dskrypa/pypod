"""
AFC Client that handles communicating with the iDevice.

:author: Doug Skrypa (original: dark[-at-]gotohack.org)
"""

import logging
from struct import unpack, unpack_from, pack, Struct
from typing import TYPE_CHECKING, Union, Tuple, Dict, Optional

from construct.lib.containers import Container
from construct import Const, Int64ul, core as construct_core

from ..lockdown import LockdownClient
from .constants import *  # noqa
from .exceptions import iOSError

if TYPE_CHECKING:
    from ..plist_service import PlistService

__all__ = ['AFCClient', 'AFC2Client']
log = logging.getLogger(__name__)

AFCPacket = construct_core.Struct(
    magic=Const(AFCMAGIC),
    entire_length=Int64ul,
    this_length=Int64ul,
    packet_num=Int64ul,
    operation=Int64ul,
)
UInt64 = Struct('<Q').pack
build_header = AFCPacket.build
parse_header = AFCPacket.parse


class AFCClient:
    def __init__(self, lockdown=None, serviceName='com.apple.afc', service=None, udid=None):
        self.serviceName = serviceName
        self.lockdown = lockdown or LockdownClient(udid=udid)
        self.service = service or self.lockdown.startService(self.serviceName)  # type: PlistService
        self._send = self.service.sock.send
        self._recv = self.service.recv_exact
        self._packet_num = 0
        # https://docs.python.org/3/library/struct.html#format-characters

    def stop_session(self):
        log.debug('Disconnecting...')
        self.service.close()

    def dispatch_packet(self, operation, data: Union[bytes, str], length: Optional[int] = None):
        actual_len = 40 + len(data)
        header = build_header(Container(
            magic=AFCMAGIC,
            entire_length=actual_len,
            this_length=length or actual_len,
            packet_num=self._packet_num,
            operation=operation
        ))
        self._packet_num += 1
        if isinstance(data, str):
            data = data.encode('utf-8')
        self._send(header)
        self._send(data)

    def receive_data(self) -> Tuple[int, bytes]:
        data = b''
        status = AFC_E_SUCCESS
        if raw := self._recv(40):
            resp = parse_header(raw)
            entire_length = resp['entire_length']
            assert entire_length >= 40
            data = self._recv(entire_length - 40)
            if resp.operation == AFC_OP_STATUS:
                status = unpack_from('<Q', data)[0]

        return status, data

    def do_operation(self, opcode, data: Union[bytes, str] = b'', suffix: str = '') -> bytes:
        self.dispatch_packet(opcode, data)
        status, data = self.receive_data()
        if status != AFC_E_SUCCESS:
            code = AFC_OPERATION_NAMES.get(opcode, opcode)
            raise iOSError(None, status, f'Error processing request with {code=} {suffix}'.strip())
        return data

    def get_device_info(self):
        return _as_dict(self.do_operation(AFC_OP_GET_DEVINFO))

    def listdir(self, dirname):
        data = self.do_operation(AFC_OP_READ_DIR, dirname, f'for {dirname=!r}')
        return list(filter(None, data.decode('utf-8').split('\x00')))

    def make_directory(self, dirname):
        self.do_operation(AFC_OP_MAKE_DIR, dirname, f'for {dirname=!r}')

    def get_file_info(self, filename):
        data = self.do_operation(AFC_OP_GET_FILE_INFO, filename, f'for {filename=!r}')
        return _as_dict(data)

    def make_link(self, target: str, name: str, link_type=AFC_SYMLINK):
        self.do_operation(
            AFC_OP_MAKE_LINK,
            UInt64(link_type) + target.encode('utf-8') + b'\x00' + name.encode('utf-8') + b'\x00',
            f'for {target=!r} {name=!r}'
        )

    def file_open(self, path: str, mode=AFC_FOPEN_RDONLY):
        data = self.do_operation(AFC_OP_FILE_OPEN, UInt64(mode) + path.encode('utf-8') + b'\x00', f'for {path=!r}')
        return unpack('<Q', data)[0] if data else None

    def file_close(self, handle):
        self.do_operation(AFC_OP_FILE_CLOSE, UInt64(handle), f'for {handle=!r}')

    def file_tell(self, handle):
        data = self.do_operation(AFC_OP_FILE_TELL, UInt64(handle), f'for {handle=!r}')
        return unpack('<Q', data)[0]

    def file_seek(self, handle, offset: int, whence: int = 0):
        """
        :param handle: A file handle obtained from :meth:`.file_open`
        :param int offset: The byte offset within the file to seek to
        :param int whence: Seek direction - one of SEEK_SET, SEEK_CUR, or SEEK_END
        :return: AFC_E_SUCCESS on success or an AFC_E_* error value
        """
        data = self.do_operation(
            AFC_OP_FILE_SEEK, pack('<QQQ', handle, whence, offset), f'for {handle=!r}'
        )
        return unpack('<Q', data)[0]

    def file_truncate(self, handle, size: Optional[int] = None):
        # Based on https://github.com/bryanforbes/libimobiledevice/blob/master/src/afc.c#L1149
        if size is None:
            size = self.file_tell(handle)
        data = self.do_operation(AFC_OP_FILE_SET_SIZE, pack('<QQ', handle, size), f'for {handle=!r}')
        return unpack('<Q', data)[0]

    def file_set_mtime(self, path: str, mtime: int):
        # Note: For some reason, changing this results in the st_birthtime changing to 0
        if mtime < 2_000_000_000:
            mtime *= 1_000_000_000
        self.do_operation(AFC_OP_SET_FILE_TIME, UInt64(mtime) + path.encode('utf-8') + b'\x00', f'for {path=!r}')

    def remove(self, filename):
        filename = filename.encode('utf-8')
        self.do_operation(AFC_OP_REMOVE_PATH, filename + b'\x00', f'for {filename=!r}')

    def rename(self, old: str, new: str):
        old = old.encode('utf-8')
        new = new.encode('utf-8')
        self.do_operation(AFC_OP_RENAME_PATH, old + b'\x00' + new + b'\x00', f'for {old=!r} {new=!r}')


class AFC2Client(AFCClient):
    def __init__(self, lockdown=None, *args, **kwargs):
        super().__init__(lockdown, 'com.apple.afc2', *args, **kwargs)


def _as_dict(data: bytes) -> Dict[str, str]:
    parts = data.decode('utf-8').split('\x00')[:-1]
    assert len(parts) % 2 == 0
    iparts = iter(parts)
    return dict(zip(iparts, iparts))
