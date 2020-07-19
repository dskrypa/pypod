"""
A basic shell implementation to facilitate browsing files on an iPod.

:author: Doug Skrypa
"""

import logging
import sys
from datetime import datetime
from itertools import count
from pathlib import Path
from traceback import print_exc, format_exc
from typing import Optional

from prompt_toolkit import PromptSession, ANSI
from prompt_toolkit.history import FileHistory

from ..idevice import iDevice, iPath
from .color import colored
from .commands import run_shell_command
from .completion import FileCompleter
from .exceptions import ExitLoop, CommandError
from .utils import Terminal

__all__ = ['iDeviceShell']
log = logging.getLogger(__name__)
HISTORY_PATH = Path('~/.config/pypod/idevice_shell.history').expanduser()


class iDeviceShell:
    def __init__(self, ipod: Optional[iDevice] = None):
        self.ipod = ipod or iDevice.find()
        self._ps1 = '{} iPod[{}]: {} {}{} '.format(
            colored('{}', 11), colored(self.ipod.name, 14), colored('{}', 11), colored('{}', 10), colored('$', 11)
        )
        self.cwd = self.ipod.get_path('/')  # type: iPath
        self.completer = FileCompleter()
        if not HISTORY_PATH.exists():
            HISTORY_PATH.parent.mkdir(parents=True)
        self.session = PromptSession(history=FileHistory(HISTORY_PATH.as_posix()))
        self._term = Terminal()
        print(colored('=' * (self._term.width - 1), 6))
        self._num = count(len(self.session.history._loaded_strings))

    def cmdloop(self, intro: Optional[str] = None):
        print(intro or f'Interactive iPod Session - Connected to: {self.ipod}')
        while True:
            try:
                self._handle_input()
            except KeyboardInterrupt:
                pass
            except ExitLoop:
                break

    def _handle_input(self):
        prompt = self._ps1.format(datetime.now().strftime('[%H:%M:%S]'), self.cwd, next(self._num))
        # noinspection PyTypeChecker
        if input_line := self.session.prompt(ANSI(prompt), completer=self.completer(self.cwd)).strip():
            try:
                if cwd := run_shell_command(self.cwd, input_line):
                    self.cwd = cwd
            except ExitLoop:
                raise
            except CommandError as e:
                log.debug(format_exc())
                print(e, file=sys.stderr)
            except Exception as e:
                print_exc()
                print(colored(f'Unexpected error: {e}', 9), file=sys.stderr)
