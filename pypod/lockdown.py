"""
Lockdown client - handles pairing with an iDevice.

:author: Doug Skrypa (original: dark[-at-]gotohack.org)
"""

import os
import plistlib
import sys
import uuid
import platform
import logging
from distutils.version import LooseVersion
from pathlib import Path
from typing import Optional, Dict, Any

from .exceptions import PairingError, NotTrustedError, FatalPairingError, NotPairedError, CannotStopSessionError
from .exceptions import StartServiceError, InitializationError
from .plist_service import PlistService
from .ssl import make_certs_and_key
from .usbmux import MuxDevice, UsbmuxdClient

__all__ = ['LockdownClient']
log = logging.getLogger(__name__)


class LockdownClient:
    def __init__(
        self,
        udid: Optional[str] = None,
        device: Optional[MuxDevice] = None,
        cache_dir: str = '.cache/pymobiledevice',
    ):
        self.cache_dir = cache_dir
        self.record = None  # type: Optional[Dict[str, Any]]
        self.sslfile = None
        self.paired = False
        self.session_id = None
        self.svc = PlistService(62078, udid, device)
        self.hostID = self.SystemBUID = str(uuid.uuid3(uuid.NAMESPACE_DNS, platform.node())).upper()
        self.label = 'pyMobileDevice'

        assert self.queryType() == 'com.apple.mobile.lockdown'

        self.allValues = self.getValue()
        self.udid = self.allValues.get('UniqueDeviceID').replace('-', '')
        self.UniqueChipID = self.allValues.get('UniqueChipID')
        self.DevicePublicKey = self.allValues.get('DevicePublicKey')
        self.ios_version = LooseVersion(self.allValues.get('ProductVersion'))
        self.identifier = self.udid
        if not self.identifier:
            if self.UniqueChipID:
                self.identifier = '%x' % self.UniqueChipID
            else:
                raise InitializationError('Could not get UDID or ECID, failing')

        if not self.validate_pairing():
            self.pair()
            self.svc = PlistService(62078, udid, device)
            if not self.validate_pairing():
                raise FatalPairingError
        self.paired = True

    def queryType(self):
        return self.svc.plist_request({'Request': 'QueryType'}).get('Type')

    def validate_pairing(self):
        folder = _get_lockdown_dir()
        try:
            pair_record = plistlib.load(folder + '%s.plist' % self.identifier)
            log.debug('Using iTunes pair record: %s.plist' % self.identifier)
        except Exception:
            log.debug('No iTunes pairing record found for device %s' % self.identifier)
            if self.ios_version > LooseVersion('13.0'):
                log.debug('Getting pair record from usbmuxd')
                pair_record = UsbmuxdClient().get_pair_record(self.udid)
            else:
                log.debug('Looking for pymobiledevice pairing record')
                if record := read_home_file(self.cache_dir, '%s.plist' % self.identifier):
                    pair_record = plistlib.loads(record)
                    log.debug('Found pymobiledevice pairing record for device %s' % self.udid)
                else:
                    log.debug('No pymobiledevice pairing record found for device %s' % self.identifier)
                    return False

        self.record = pair_record
        certPem = pair_record['HostCertificate']
        privateKeyPem = pair_record['HostPrivateKey']

        if self.ios_version < LooseVersion('11.0'):
            validate_pair = {'Label': self.label, 'Request': 'ValidatePair', 'PairRecord': pair_record}
            resp = self.svc.plist_request(validate_pair)
            if not resp or 'Error' in resp:
                log.error(f'Failed to ValidatePair: {resp}')
                return False

        self.hostID = pair_record.get('HostID', self.hostID)
        self.SystemBUID = pair_record.get('SystemBUID', self.SystemBUID)

        d = {'Label': self.label, 'Request': 'StartSession', 'HostID': self.hostID, 'SystemBUID': self.SystemBUID}
        resp = self.svc.plist_request(d)
        self.session_id = resp.get('SessionID')
        if resp.get('EnableSessionSSL'):
            self.sslfile = self.identifier + '_ssl.txt'
            self.sslfile = write_home_file(self.cache_dir, self.sslfile, certPem + b'\n' + privateKeyPem)
            self.svc.ssl_start(self.sslfile, self.sslfile)

        self.paired = True
        return True

    def pair(self):
        self.DevicePublicKey = self.getValue('', 'DevicePublicKey')
        if self.DevicePublicKey == '':
            log.error('Unable to retrieve DevicePublicKey')
            return False

        log.debug('Creating host key & certificate')
        cert_pem, priv_key_pem, dev_cert_pem = make_certs_and_key(self.DevicePublicKey)
        pair_record = {
            'DevicePublicKey': plistlib.Data(self.DevicePublicKey),
            'DeviceCertificate': plistlib.Data(dev_cert_pem),
            'HostCertificate': plistlib.Data(cert_pem),
            'HostID': self.hostID,
            'RootCertificate': plistlib.Data(cert_pem),
            'SystemBUID': '30142955-444094379208051516'
        }

        pair = self.svc.plist_request({'Label': self.label, 'Request': 'Pair', 'PairRecord': pair_record})
        if pair and pair.get('Result') == 'Success' or 'EscrowBag' in pair:
            pair_record['HostPrivateKey'] = plistlib.Data(priv_key_pem)
            pair_record['EscrowBag'] = pair.get('EscrowBag')
            write_home_file(self.cache_dir, '%s.plist' % self.identifier, plistlib.dumps(pair_record))
            self.paired = True
            return True
        elif pair and pair.get('Error') == 'PasswordProtected':
            self.svc.close()
            raise NotTrustedError
        else:
            log.error(pair.get('Error'))
            self.svc.close()
            raise PairingError

    def getValue(self, domain=None, key=None):
        if isinstance(key, str) and hasattr(self, 'record') and hasattr(self.record, key):
            return self.record[key]

        req = {'Request': 'GetValue', 'Label': self.label}
        if domain:
            req['Domain'] = domain
        if key:
            req['Key'] = key

        if resp := self.svc.plist_request(req):
            r = resp.get('Value')
            if hasattr(r, 'data'):
                return r.data
            return r

    def setValue(self, value, domain=None, key=None):
        req = {'Request': 'SetValue', 'Label': self.label, 'Value': value}
        if domain:
            req['Domain'] = domain
        if key:
            req['Key'] = key

        resp = self.svc.plist_request(req)
        log.debug(resp)
        return resp

    def startService(self, name, escrow_bag=None) -> PlistService:
        if not self.paired:
            log.warning('NotPaired')
            raise NotPairedError

        req = {'Label': self.label, 'Request': 'StartService', 'Service': name}
        if escrow_bag:
            req['EscrowBag'] = escrow_bag

        if not (resp := self.svc.plist_request(req)):
            raise StartServiceError(f'Unable to start service={name!r}')
        elif error := resp.get('Error'):
            if error == 'PasswordProtected':
                raise StartServiceError(f'Unable to start service={name!r} - a password must be entered on the device')
            raise StartServiceError(f'Unable to start service={name!r} - {error=!r}')

        plist_service = PlistService(resp.get('Port'), self.udid)
        if resp.get('EnableServiceSSL', False):
            plist_service.ssl_start(self.sslfile, self.sslfile)
        return plist_service

    def startServiceWithEscrowBag(self, name, escrowBag=None) -> PlistService:
        return self.startService(name, escrowBag or self.record['EscrowBag'])

    def stop_session(self):
        if self.session_id and self.svc:
            resp = self.svc.plist_request({'Label': self.label, 'Request': 'StopSession', 'SessionID': self.session_id})
            self.session_id = None
            if not resp or resp.get('Result') != 'Success':
                raise CannotStopSessionError(resp)
            return resp

    def enter_recovery(self):
        log.debug(self.svc.plist_request({'Request': 'EnterRecovery'}))


def get_home_path(foldername, filename):
    path = Path('~').expanduser().joinpath(foldername)
    if not path.exists():
        path.mkdir(parents=True)
    return path.joinpath(filename)


def read_home_file(foldername, filename):
    path = get_home_path(foldername, filename)
    if not path.exists():
        return None
    with path.open('rb') as f:
        return f.read()


def write_home_file(foldername, filename, data):
    path = get_home_path(foldername, filename)
    with path.open('wb') as f:
        f.write(data)
    return path.as_posix()


def _get_lockdown_dir():
    if sys.platform == 'win32':
        return os.environ['ALLUSERSPROFILE'] + '/Apple/Lockdown/'
    else:
        return '/var/db/lockdown/'
