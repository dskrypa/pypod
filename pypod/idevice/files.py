"""
This module implements file IO functionality for files that exist on an iPod and are accessed via an AFC client as if
they were native files opened via :func:`open`.

:author: Doug Skrypa
"""

import logging
from io import RawIOBase, BufferedReader, BufferedWriter, BufferedRWPair, TextIOWrapper
from typing import TYPE_CHECKING, Optional
from weakref import finalize

# noinspection PyPackageRequirements
from ..core import AFCClient, iDeviceFileClosed
from ..core.constants import (
    AFC_FOPEN_RDONLY, AFC_FOPEN_RW, AFC_FOPEN_WRONLY, AFC_FOPEN_WR, AFC_FOPEN_APPEND, AFC_FOPEN_RDAPPEND
)

if TYPE_CHECKING:
    from .path import iPath

__all__ = ['open_ipod_file']
log = logging.getLogger(__name__)

FILE_MODES = {
    'r': AFC_FOPEN_RDONLY,
    'r+': AFC_FOPEN_RW,
    'w': AFC_FOPEN_WRONLY,
    'w+': AFC_FOPEN_WR,
    'a': AFC_FOPEN_APPEND,
    'a+': AFC_FOPEN_RDAPPEND,
}
CAN_READ = (AFC_FOPEN_RDONLY, AFC_FOPEN_RW, AFC_FOPEN_WR, AFC_FOPEN_RDAPPEND)
CAN_WRITE = (AFC_FOPEN_RW, AFC_FOPEN_WRONLY, AFC_FOPEN_WR, AFC_FOPEN_APPEND, AFC_FOPEN_RDAPPEND)


def open_ipod_file(path: 'iPath', mode: str = 'r', encoding=None, newline=None):
    # log.info(f'open_ipod_file({path=!r}, {mode=:x}, {encoding=:x}, {newline=!r})')
    orig_mode = mode
    if mode.endswith('b'):
        encoding = None
        newline = b'\n'
        mode = mode[:-1]
    else:
        encoding = encoding or 'utf-8'
        newline = newline or '\n'
        if mode.endswith('t'):
            mode = mode[:-1]
    try:
        mode = FILE_MODES[mode]
    except KeyError as e:
        raise ValueError(f'Invalid mode={orig_mode}')

    if encoding:
        read = mode in CAN_READ
        write = mode in CAN_WRITE
        if read and write:
            buffered = iBufferedRWPair(iPodIOBase(path, AFC_FOPEN_RDONLY), iPodIOBase(path, mode))
        elif write:
            buffered = iBufferedWriter(iPodIOBase(path, mode))
        else:
            buffered = iBufferedReader(iPodIOBase(path, mode))
        # noinspection PyTypeChecker
        return iTextIOWrapper(buffered, encoding=encoding, newline=newline)
    else:
        return iPodIOBase(path, mode)


class iPodIOBase(RawIOBase):
    def __init__(self, path: 'iPath', mode: int):
        self.encoding = None
        self._mode = mode
        self._path = path
        self._afc = path._ipod.afc  # type: AFCClient
        self._f = self._afc.file_open(path.as_posix(), mode)
        self.__finalizer = finalize(self, self.__close)

    def fileno(self):
        return self._f

    @property
    def closed(self):
        return self._f is None

    def __close(self):
        if not self.closed:
            self._afc.file_close(self._f)
            self._f = None

    def close(self):
        if self.__finalizer.detach():
            self.__close()

    def __del__(self):
        self.close()

    def read(self, size=-1) -> bytes:
        if self.closed:
            raise iDeviceFileClosed(self._path)
        return self._afc.read(self._path.as_posix(), self._f, size)

    def write(self, data: bytes):
        if self.closed:
            raise iDeviceFileClosed(self._path)
        return self._afc.write(self._path.as_posix(), data, self._f)

    def flush(self):
        return None

    def isatty(self):
        return False

    def readable(self):
        return not self.closed and self._mode in CAN_READ

    def writable(self):
        return not self.closed and self._mode in CAN_WRITE

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        return self._afc.file_seek(self._f, offset, whence)

    def tell(self):
        return self._afc.file_tell(self._f)

    def truncate(self, size: Optional[int] = None):
        self._afc.file_truncate(self._f, size)


# noinspection PyUnresolvedReferences
class BufferedIOMixin:
    def read(self, size=-1):
        return self.raw.read(size)

    def write(self, data):
        return self.raw.write(data)

    def readline(self, size=-1):
        return self.raw.readline(size)


class iBufferedReader(BufferedIOMixin, BufferedReader):
    pass


class iBufferedWriter(BufferedIOMixin, BufferedWriter):
    pass


class iBufferedRWPair(BufferedIOMixin, BufferedRWPair):
    pass


class iTextIOWrapper(TextIOWrapper):
    def read(self, size=-1):
        return self.buffer.read(size).decode(self.encoding)

    def write(self, data: str):
        return self.buffer.write(data.encode(self.encoding))

    def readline(self, size=-1):
        return self.buffer.readline(size).decode(self.encoding)
