
from errno import ENOENT, ENOTDIR

from .constants import AFC_E_UNKNOWN_ERROR, AFC_ERROR_NAMES, AFC_E_OBJECT_NOT_FOUND, AFC_E_OBJECT_IS_DIR

__all__ = ['iOSError']

AFC_TO_OS_ERROR_CODES = {
    AFC_E_OBJECT_NOT_FOUND: ENOENT,
    AFC_E_OBJECT_IS_DIR: ENOTDIR,
}


class iOSError(OSError):
    """Generic exception for AFC errors or errors that would normally be raised by the OS"""
    def __init__(self, errno, afc_errno=AFC_E_UNKNOWN_ERROR, *args, **kwargs):
        errno = AFC_TO_OS_ERROR_CODES.get(afc_errno) if errno is None else errno
        # noinspection PyArgumentList
        super().__init__(errno, *args, **kwargs)
        self.afc_errno = afc_errno

    def __str__(self):
        name = AFC_ERROR_NAMES.get(self.afc_errno, 'UNKNOWN ERROR')
        return f'{self.__class__.__name__}[afc={self.afc_errno}/{name}][os={self.errno}] {self.strerror}'
