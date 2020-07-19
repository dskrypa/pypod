
import logging
from functools import cached_property
from typing import Optional
from weakref import finalize

from ..afc import AFCClient
from ..lockdown import LockdownClient
from ..usbmux import USBMux
from ..utils import DictAttrProperty

__all__ = ['iDevice']
log = logging.getLogger(__name__)


class iDevice:
    _instance: Optional['iDevice'] = None
    name = DictAttrProperty('info', 'DeviceName')
    ios_version = DictAttrProperty('info', 'ProductVersion')

    def __init__(self, udid: str):
        self.udid = udid
        self.__finalizer = finalize(self, self.__close)

    def __repr__(self):
        return f'<{self.__class__.__name__}[name={self.name!r}, ios_version={self.ios_version}]>'

    @classmethod
    def find(cls, serial: Optional[str] = None, timeout=0.1, max_attempts=5) -> 'iDevice':
        if cls._instance is None or (serial and cls._instance.udid != serial):
            device = USBMux().find_device(serial, timeout, max_attempts)
            log.debug(f'Found {device=}')
            cls._instance = cls(device.serial)
        return cls._instance

    @cached_property
    def _lockdown(self):
        return LockdownClient(self.udid)

    @cached_property
    def _afc_plist_svc(self):
        return self._lockdown.start_service('com.apple.afc')

    @cached_property
    def afc(self) -> AFCClient:
        return AFCClient(service=self._afc_plist_svc)

    @cached_property
    def info(self):
        return self._lockdown.device_info

    def get_path(self, path: str) -> 'iPath':
        return iPath(path, ipod=self)

    def close(self):
        if self.__finalizer.detach():
            self.__close()

    def __close(self):
        if 'afc' in self.__dict__:
            log.debug('Stopping afc service...')
            self.afc.close()
            del self.__dict__['afc']

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()


# Down here due to circular import
from .path import iPath
