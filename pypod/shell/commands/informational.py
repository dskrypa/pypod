
import logging

from ..argparse import ShellArgParser
from .base import ShellCommand
from .utils import Printer

log = logging.getLogger(__name__)


class Info(ShellCommand, cmd='info'):
    parser = ShellArgParser('info', description='Display device info')
    parser.add_argument('--format', '-f', dest='out_fmt', choices=Printer.formats, default='yaml', help='The output format to use')

    def __call__(self, out_fmt='yaml'):
        Printer(out_fmt).pprint(self.ipod.info)
