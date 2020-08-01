
import json
import logging
import shlex
from abc import ABC, abstractmethod
from argparse import REMAINDER
from io import TextIOBase, RawIOBase
from pathlib import Path
from sys import stdout as out, stderr as err
from typing import Dict, Iterable, List, Optional, Union, Type, Any, Sequence, MutableMapping, Mapping, Tuple

from ...core.exceptions import iOSError
from ...idevice.path import iPath
from ..argparse import ShellArgParser
from ..exceptions import ArgError, ExitLoop, UnknownCommand, ExecutionError, ConfigError

__all__ = ['run_shell_command']
log = logging.getLogger(__name__)

CONFIG_PATH = Path('~/.config/pypod').expanduser()
IO = Union[TextIOBase, RawIOBase]


def run_shell_command(cwd: iPath, input_str: str, env: MutableMapping[str, Any]) -> Optional[iPath]:
    name, raw_args = resolve_aliases(input_str, env.get('aliases'))
    try:
        cmd_cls = ShellCommand._commands[name]
    except KeyError:
        if name.startswith('#'):
            return None
        raise UnknownCommand(name)
    # noinspection PyUnresolvedReferences
    kwargs = cmd_cls.parser.parse_kwargs(raw_args)
    try:
        return cmd_cls(cwd, env)(**kwargs)
    except iOSError as e:
        raise ExecutionError(name, e)


def resolve_aliases(input_str: str, aliases: Optional[Mapping[str, str]]) -> Tuple[str, Sequence[str]]:
    name, *raw_args = shlex.split(input_str)

    if aliases:
        i = 0
        while alias := aliases.get(name):
            if i > 30:
                raise ConfigError(f'Possible infinite alias loop detected: {alias=!r} {name=!r}')
            name, *extra = shlex.split(alias)
            raw_args = extra + raw_args
            i += 1

    return name, raw_args


class ShellCommand(ABC):
    _commands: Dict[str, Type['ShellCommand']] = {}
    name: Optional[str] = None

    # noinspection PyMethodOverriding
    def __init_subclass__(cls, cmd):
        cls.name = cmd
        ShellCommand._commands[cmd] = cls

    def __init__(
        self, cwd: iPath, env: MutableMapping[str, Any], stdin: Optional[IO] = None, stdout: IO = out, stderr: IO = err
    ):
        self.cwd = cwd
        self.env = env
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


class Alias(ShellCommand, cmd='alias'):
    parser = ShellArgParser('alias', description='Store a command alias')
    parser.add_argument('alias', nargs='?', help='The text to use as an alias')
    mgroup = parser.add_mutually_exclusive_group()
    mgroup.add_argument('--remove', '-r', action='store_true', help='Remove the specified alias')
    mgroup.add_argument('--list', '-l', action='store_true', help='List existing aliases')
    parser.add_argument('command', nargs=REMAINDER, help='The command with which alias should be replaced')

    def __call__(self, alias: str, command: Sequence[str], remove: bool, list: bool):
        if list:
            self.list_aliases()
        elif not alias:
            raise ArgError(f'{self.name}: An alias must be specified')
        elif remove:
            self.remove_alias(alias)
        else:
            if not command:
                raise ArgError(f'{self.name}: A command must be specified')
            self.add_alias(alias, ' '.join(command))

    def list_aliases(self):
        aliases = self.env.setdefault('aliases', {})
        for alias, command in sorted(aliases.items()):
            self.print(f'{alias} => {command!r}')

    def add_alias(self, alias: str, command: str):
        aliases = self.env.setdefault('aliases', {})
        self.print(f'{self.name}: {alias} => {command!r}')
        aliases[alias] = command
        self._save(aliases)

    def remove_alias(self, alias: str):
        aliases = self.env.setdefault('aliases', {})
        try:
            del aliases[alias]
        except KeyError:
            raise ArgError(f'{self.name}: {alias!r} does not exist')
        else:
            self.print(f'{self.name}: Removed {alias!r}')
            self._save(aliases)

    def _save(self, aliases):
        with CONFIG_PATH.joinpath('aliases.json').open('w', encoding='utf-8') as f:
            json.dump(aliases, f, ensure_ascii=False, indent=4, sort_keys=True)
