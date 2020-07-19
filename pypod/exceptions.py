

class PairingError(Exception):
    pass


class NotTrustedError(PairingError):
    pass


class FatalPairingError(PairingError):
    pass


class NotPairedError(Exception):
    pass


class CannotStopSessionError(Exception):
    pass


class StartServiceError(Exception):
    pass


class InitializationError(Exception):
    pass
