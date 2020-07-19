"""
AFC Client that handles communicating with the iDevice.

:author: Doug Skrypa (original: dark[-at-]gotohack.org)
"""

import logging
from errno import ENOTEMPTY
from struct import unpack, unpack_from, pack, Struct
from typing import TYPE_CHECKING, Union, Tuple, Dict, Optional

from construct.lib.containers import Container
from construct import Const, Int64ul, core as construct_core

from .lockdown import LockdownClient
from .constants import *  # noqa
from .exceptions import iOSError, iFileNotFoundError, iDeviceIOException

if TYPE_CHECKING:
    from .plist_service import PlistService

__all__ = ['AFCClient', 'AFC2Client']
log = logging.getLogger(__name__)

MAXIMUM_READ_SIZE = 1 << 16
MAXIMUM_WRITE_SIZE = 1 << 15
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
        self._handles = {}
        # https://docs.python.org/3/library/struct.html#format-characters

    def stop_session(self):
        log.debug('Disconnecting...')
        self.service.close()

    def dispatch_packet(self, operation: int, data: Union[bytes, str], length: Optional[int] = None):
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
        if header := self._recv(40):
            resp = parse_header(header)
            entire_length = resp['entire_length']
            assert entire_length >= 40
            data = self._recv(entire_length - 40)
            if resp.operation == AFC_OP_STATUS:
                status = unpack_from('<Q', data)[0]

        return status, data

    def do_operation(
        self,
        operation: int,
        data: Union[bytes, str] = b'',
        suffix: str = '',
        path: str = '',
        length: Optional[int] = None,
    ) -> bytes:
        self.dispatch_packet(operation, data, length)
        status, data = self.receive_data()
        if status != AFC_E_SUCCESS:
            if status == AFC_E_OBJECT_NOT_FOUND and path:
                raise iFileNotFoundError(f'File/directory does not exist: {path}'.strip())
            code = AFC_OPERATION_NAMES.get(operation, operation)
            raise iOSError(None, status, f'Error processing request with {code=} {suffix}'.strip())
        return data

    def get_device_info(self):
        return _as_dict(self.do_operation(AFC_OP_GET_DEVINFO))

    def listdir(self, path: str):
        data = self.do_operation(AFC_OP_READ_DIR, path, f'for {path=!r}', path)
        return list(filter(None, data.decode('utf-8').split('\x00')))

    def make_directory(self, path: str):
        self.do_operation(AFC_OP_MAKE_DIR, path, f'for {path=!r}', path)

    def get_stat_dict(self, path: str):
        data = self.do_operation(AFC_OP_GET_FILE_INFO, path, f'for {path=!r}', path)
        return _as_dict(data)

    def make_link(self, target: str, name: str, link_type=AFC_SYMLINK):
        self.do_operation(
            AFC_OP_MAKE_LINK,
            UInt64(link_type) + target.encode('utf-8') + b'\x00' + name.encode('utf-8') + b'\x00',
            f'for {target=!r} {name=!r}'
        )

    def file_open(self, path: str, mode: int = AFC_FOPEN_RDONLY):
        data = self.do_operation(
            AFC_OP_FILE_OPEN, UInt64(mode) + path.encode('utf-8') + b'\x00', f'for {path=!r}', path
        )
        handle = unpack('<Q', data)[0]
        self._handles[handle] = path
        return handle

    def _handle_path(self, handle: int) -> str:
        try:
            return self._handles[handle]
        except KeyError as e:
            raise iOSError(None, None, f'Unknown file {handle=}')

    def file_close(self, handle: int):
        path = self._handles.pop(handle, '(UNKNOWN)')
        self.do_operation(AFC_OP_FILE_CLOSE, UInt64(handle), f'for {path=!r} ({handle=!r})')

    def file_tell(self, handle: int):
        data = self.do_operation(AFC_OP_FILE_TELL, UInt64(handle), f'for {handle=!r}')
        return unpack('<Q', data)[0]

    def file_seek(self, handle: int, offset: int, whence: int = 0):
        """
        :param handle: A file handle obtained from :meth:`.file_open`
        :param int offset: The byte offset within the file to seek to
        :param int whence: Seek direction - one of SEEK_SET, SEEK_CUR, or SEEK_END
        :return: AFC_E_SUCCESS on success or an AFC_E_* error value
        """
        # absolute position is calculated here instead of passing whence value because it doesn't seem to support
        # negative offsets
        if whence == 0:  # absolute
            absolute = offset
            path = None
        elif whence == 1:  # from current position
            absolute = self.file_tell(handle) + offset
            path = self._handle_path(handle)
            size = self._get_size(path)
            if absolute < 0 or absolute > size:
                raise iOSError(None, None, f'Resolved {absolute=} for {offset=} of {path=!r} is invalid ({size=})')
            log.debug(f'{path}: Seeking to pos={absolute} ({size=})')
        elif whence == 2:  # from end
            if offset > 0:
                raise iOSError(None, None, f'Invalid {offset=!r} for whence=2')
            path = self._handle_path(handle)
            size = self._get_size(path)
            absolute = size + offset
            log.debug(f'{path}: Seeking to pos={absolute} ({size=})')
        else:
            raise iOSError(None, None, f'Invalid {whence=!r} - must be 0, 1, or 2')

        self.do_operation(AFC_OP_FILE_SEEK, pack('<QQQ', handle, 0, absolute), f'for {handle=!r}', path)
        return absolute

    def file_truncate(self, handle: int, size: Optional[int] = None):
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

    def _is_dir(self, path: str):
        try:
            stat_dict = self.get_stat_dict(path)
        except iOSError:
            return False
        return stat_dict.get('st_ifmt') == 'S_IFDIR'

    def _is_empty(self, path: str):
        contents = self.listdir(path)
        return len(contents) == 2 and '.' in contents and '..' in contents

    def _get_size(self, path: str) -> int:
        return int(self.get_stat_dict(path)['st_size'])

    def remove(self, path: str):
        try:
            self.do_operation(AFC_OP_REMOVE_PATH, path.encode('utf-8') + b'\x00', f'for {path=!r}', path)
        except iOSError as e:
            if e.errno is None and self._is_dir(path) and not self._is_empty(path):
                e.errno = ENOTEMPTY
                e.strerror = f'The directory is not empty: {path}'
            raise

    def rename(self, old: str, new: str):
        old = old.encode('utf-8')
        new = new.encode('utf-8')
        self.do_operation(AFC_OP_RENAME_PATH, old + b'\x00' + new + b'\x00', f'for {old=!r} {new=!r}')

    def read(self, path: str, handle: Optional[int] = None, size: int = -1) -> bytes:
        if handle and not path:
            path = self._handle_path(handle)
        elif handle is None:
            handle = self.file_open(path, AFC_FOPEN_RDONLY)

        remaining = self._get_size(path) - self.file_tell(handle)
        if remaining < size or size < 0:
            size = remaining

        suffix = f'for {path=!r}'
        data = bytearray(size)
        view = memoryview(data)
        pos = 0
        while size > 0:
            chunk_size = MAXIMUM_READ_SIZE if size > MAXIMUM_READ_SIZE else size
            next_pos = pos + chunk_size
            view[pos:next_pos] = self.do_operation(AFC_OP_READ, pack('<QQ', handle, chunk_size), suffix, path)
            size -= chunk_size
            pos = next_pos
        return data

    def write(self, path: str, data: Union[bytes, str], handle: Optional[int] = None) -> int:
        if isinstance(data, str):
            data = data.encode('utf-8')
        if handle is None:
            handle = self.file_open(path, AFC_FOPEN_WRONLY)

        suffix = f'for {path=!r}'
        _handle = pack('<Q', handle)
        pos = 0
        view = memoryview(data)
        length = remaining = len(data)
        while remaining > 0:
            chunk_size = remaining if remaining < MAXIMUM_WRITE_SIZE else MAXIMUM_WRITE_SIZE
            next_pos = pos + chunk_size
            # noinspection PyTypeChecker
            self.do_operation(AFC_OP_WRITE, _handle + view[pos:next_pos], suffix, path, length=48)
            remaining -= chunk_size
            pos = next_pos
        return length


class AFC2Client(AFCClient):
    def __init__(self, lockdown=None, *args, **kwargs):
        super().__init__(lockdown, 'com.apple.afc2', *args, **kwargs)


def _as_dict(data: bytes) -> Dict[str, str]:
    parts = data.decode('utf-8').split('\x00')[:-1]
    assert len(parts) % 2 == 0
    iparts = iter(parts)
    return dict(zip(iparts, iparts))
