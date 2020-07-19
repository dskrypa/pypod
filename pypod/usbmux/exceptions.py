class MuxError(Exception):
    pass


class MuxVersionError(MuxError):
    pass


class NoMuxDeviceFound(MuxError):
    pass
