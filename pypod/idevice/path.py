"""
This module provides a subclass of Path that allows iPod paths accessed via an AFC client to be handled as if they were
native Path objects.

:author: Doug Skrypa
"""

import logging
import time
from functools import cached_property, partialmethod
from pathlib import Path, PurePosixPath
from stat import S_IFDIR, S_IFCHR, S_IFBLK, S_IFREG, S_IFIFO, S_IFLNK, S_IFSOCK
from typing import TYPE_CHECKING, Union, Optional

from ..core.constants import AFC_HARDLINK
from .files import open_ipod_file

if TYPE_CHECKING:
    from ..core.afc import AFCClient

__all__ = ['iPath']
log = logging.getLogger(__name__)

STAT_MODES = {
    'S_IFDIR': S_IFDIR,
    'S_IFCHR': S_IFCHR,
    'S_IFBLK': S_IFBLK,
    'S_IFREG': S_IFREG,
    'S_IFIFO': S_IFIFO,
    'S_IFLNK': S_IFLNK,
    'S_IFSOCK': S_IFSOCK,
}


class iPath(Path, PurePosixPath):
    __slots__ = ('_ipod',)

    def __new__(cls, *args, ipod=None, template=None, **kwargs):
        # noinspection PyUnresolvedReferences
        self = cls._from_parts(args, init=False)
        self._init(ipod, template)
        return self

    def _init(self, ipod=None, template: Optional['iPath'] = None):
        self._closed = False
        if template is not None:
            self._ipod = template._ipod
            self._accessor = template._accessor
        else:
            if ipod is None:
                ipod = iDevice.find()
            self._ipod = ipod
            self._accessor = iDeviceAccessor(ipod)

    open = partialmethod(open_ipod_file)  # Path.open calls io.open, which passes numeric mode/encoding to accessor.open

    def touch(self, mode=None, exist_ok=True):
        self._ipod.afc.file_set_mtime(self.resolve().as_posix(), int(time.time()))


def _str(path: Union[Path, str]) -> str:
    if isinstance(path, Path):
        return path.as_posix()
    return path


class iDeviceAccessor:
    def __init__(self, ipod):
        self.ipod = ipod
        self.afc = ipod.afc  # type: AFCClient

    def stat(self, path):
        return iDeviceStatResult(self.afc.get_stat_dict(_str(path)))

    lstat = stat

    def listdir(self, path):
        return self.afc.listdir(_str(path))

    def open(self, path, *args, **kwargs):
        raise NotImplementedError

    def scandir(self, path):
        if not isinstance(path, iPath):
            path = iPath(path, ipod=self.ipod)
        for sub_path in path.iterdir():
            # noinspection PyTypeChecker
            yield iDeviceScandirEntry(sub_path.relative_to(path))

    def chmod(self, *args, **kwargs):
        raise NotImplementedError

    lchmod = chmod

    def mkdir(self, path, mode=None, **kwargs):
        return self.afc.make_directory(_str(path))

    def rmdir(self, path):
        self.afc.remove(_str(path))

    def unlink(self, path):
        self.afc.remove(_str(path))

    def link_to(self, src, dest, **kwargs):
        self.afc.make_link(_str(src), _str(dest), AFC_HARDLINK)

    def symlink(self, src, dest, **kwargs):
        self.afc.make_link(_str(src), _str(dest))  # default is symlink

    def rename(self, src, dest, **kwargs):
        self.afc.rename(_str(src), _str(dest))

    replace = rename  # note: os.replace will overwrite the dest if it exists (I guess rename won't?)

    def utime(self, *args, **kwargs):
        raise NotImplementedError

    def readlink(self, path):
        return self.afc.readlink(_str(path))


class iDeviceStatResult:
    def __init__(self, info):
        self._info = info

    def __repr__(self):
        return 'iDeviceStatResult[{}]'.format(', '.join(f'{k}={v!r}' for k, v in self.as_dict().items()))

    def as_dict(self):
        return {k: getattr(self, k) for k in sorted(self._info)}

    def __getattr__(self, item: str):
        try:
            value = self._info[item]
        except KeyError:
            raise AttributeError(f'iDeviceStatResult has no attribute {item!r}') from None

        if item.endswith('time'):
            return int(value) // 1_000_000_000
        else:
            try:
                return int(value)
            except (ValueError, TypeError):
                return value

    @property
    def st_mode(self):
        try:
            return STAT_MODES[self.st_ifmt]
        except KeyError:
            raise AttributeError(f'Unable to convert {self.st_ifmt=!r} to st_mode')


class iDeviceScandirEntry:
    def __init__(self, path: iPath):
        self._path = path
        self._stat_sym = None   # type: Optional[iDeviceStatResult]
        self._stat = None       # type: Optional[iDeviceStatResult]

    @cached_property
    def path(self):
        return self._path.as_posix()

    @property
    def name(self):
        return self._path.name

    def stat(self, *, follow_symlinks=True):
        if self._stat is None:
            self._stat = self._path.stat()
        # noinspection PyUnresolvedReferences
        if follow_symlinks and self._stat.st_ifmt == 'S_IFLNK':
            if self._stat_sym is None:
                # noinspection PyUnresolvedReferences
                path = iPath(self._stat._info['LinkTarget'], template=self._path)
                self._stat_sym = path.stat()
            return self._stat_sym
        return self._stat

    def inode(self):
        return None

    def is_dir(self):
        return self._path.is_dir()

    def is_file(self):
        return self._path.is_file()

    def is_symlink(self):
        return self._path.is_symlink()


# Down here due to circular dependency
from .idevice import iDevice
