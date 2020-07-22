
import logging
import shlex
from abc import ABC, abstractmethod
from io import TextIOBase, RawIOBase
from pathlib import Path
from sys import stdout as out, stderr as err
from typing import Dict, Iterable, List, Optional, Union, Type, Any

from ...core.exceptions import iOSError
from ...idevice.path import iPath
from ..argparse import ShellArgParser
from ..exceptions import ArgError, ExitLoop, UnknownCommand, ExecutionError

__all__ = ['run_shell_command']
log = logging.getLogger(__name__)
IO = Union[TextIOBase, RawIOBase]


def run_shell_command(cwd: iPath, input_str: str) -> Optional[iPath]:
    # TODO: dict of aliases - replace first word in cmd with the alias
    # TODO: Store aliases in json in homedir config
    # TODO: Alias command to store a new alias in homedir config json
    name, *raw_args = shlex.split(input_str)
    try:
        cmd_cls = ShellCommand._commands[name]
    except KeyError:
        raise UnknownCommand(name)
    # noinspection PyUnresolvedReferences
    kwargs = cmd_cls.parser.parse_kwargs(raw_args)
    try:
        return cmd_cls(cwd)(**kwargs)
    except iOSError as e:
        raise ExecutionError(name, e)


class ShellCommand(ABC):
    _commands: Dict[str, Type['ShellCommand']] = {}
    name: Optional[str] = None

    # noinspection PyMethodOverriding
    def __init_subclass__(cls, cmd):
        cls.name = cmd
        ShellCommand._commands[cmd] = cls

    def __init__(self, cwd: iPath, stdin: Optional[IO] = None, stdout: IO = out, stderr: IO = err):
        self.cwd = cwd
        self.ipod = cwd._ipod
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

    @abstractmethod
    def __call__(self, **kwargs) -> Optional[iPath]:
        raise NotImplementedError

    @property
    @abstractmethod
    def parser(self) -> ShellArgParser:
        raise NotImplementedError

    def print(self, text: Any = None):
        if text is None:
            text = ''
        elif not isinstance(text, str):
            text = str(text)
        self.stdout.write(text + '\n')

    def error(self, text: Any):
        if not isinstance(text, str):
            text = str(text)
        self.stderr.write(text + '\n')

    def _rel_path(self, loc: str, allow_cwd=True) -> iPath:
        # noinspection PyUnboundLocalVariable,PyUnresolvedReferences
        if '*' in loc and (paths := list(self.cwd.glob(loc))) and len(paths) == 1:
            return paths[0]
        elif loc.startswith('~'):
            raise ArgError(f'{self.name}: Home directories are not supported for iDevice paths')
        elif loc:
            return self.cwd.joinpath(loc)
        elif allow_cwd:
            return self.cwd
        raise ArgError(f'{self.name}: A file must be specified')

    def _rel_paths(self, locs: Iterable[str], allow_cwd=True, required=False) -> List[iPath]:
        paths = []
        no_matches = []
        for loc in locs:
            last = len(paths)
            if loc.startswith('/'):
                paths.append(iPath(loc, template=self.cwd))
            else:
                paths.extend(self.cwd.glob(loc))
            if len(paths) == last:
                no_matches.append(loc)

        if not paths:
            if allow_cwd:
                paths.append(self.cwd)
            elif required:
                if no_matches:
                    raise ArgError(f'{self.name}: File does not exist: {no_matches}')
                raise ArgError(f'{self.name}: At least one file must be specified')
        return paths

    def _rel_to_cwd(self, path: iPath) -> str:
        try:
            return path.relative_to(self.cwd).as_posix()
        except Exception:
            return path.as_posix()

    def _is_file(self, path: iPath, action: str) -> bool:
        if path.is_dir():
            self.error(f'{self.name}: cannot {action} {self._rel_to_cwd(path)!r}: Is a directory')
        elif not path.exists():
            self.error(f'{self.name}: cannot {action} {self._rel_to_cwd(path)!r}: No such file or directory')
        else:
            return True
        return False

    def _get_cross_platform_paths(self, source: Iterable[str], dest: str, mode: str = 'ipod'):
        log.debug(f'_get_cross_platform_paths({source=!r}, {dest=!r}, {mode=!r})')
        if mode == 'i2p':
            sources = self._rel_paths(source, False, True)
            dest = Path(dest).expanduser().resolve()
        elif mode == 'p2i':
            sources = [Path(p).expanduser().resolve() for p in source]
            dest = self._rel_path(dest, False)
        elif mode == 'ipod':
            sources = self._rel_paths(source, False, True)
            dest = self._rel_path(dest, False)
        else:
            raise ExecutionError(self.name, f'Unexpected {mode=}')
        return sources, dest


class Exit(ShellCommand, cmd='exit'):
    parser = ShellArgParser('exit', description='Exit the shell')

    def __call__(self, **kwargs):
        raise ExitLoop


class Help(ShellCommand, cmd='help'):
    parser = ShellArgParser('help', description='Print help information')

    def __call__(self, **kwargs):
        self.print('Available commands:')
        for name, cls in sorted(self._commands.items()):
            self.print(f'{name}: {cls.parser.description}')
