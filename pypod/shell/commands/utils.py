"""
Misc utilities

:author: Doug Skrypa
"""

import json
import logging
import math
import pprint
import types
from collections import UserDict
from collections.abc import Mapping, KeysView, ValuesView, Sized, Iterable, Container
from datetime import datetime

import yaml

__all__ = ['readable_bytes', 'Printer']
log = logging.getLogger(__name__)


def readable_bytes(file_size, dec_places=None, dec_by_unit=None):
    units = list(zip(['B ', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'], [0, 2, 2, 2, 2, 2, 2, 2, 2]))
    try:
        exp = min(int(math.log(file_size, 1024)), len(units) - 1) if file_size > 0 else 0
    except TypeError as e:
        print('Invalid file size: {!r}'.format(file_size))
        raise e
    unit, dec = units[exp]
    if dec_places is not None:
        dec = dec_places
    if isinstance(dec_by_unit, dict):
        dec = dec_by_unit.get(unit, 2)
    return '{{:,.{}f}} {}'.format(dec, unit).format(file_size / 1024 ** exp)


class Printer:
    formats = ['json', 'json-pretty', 'json-compact', 'yaml', 'pprint', 'json-lines', 'plain', 'pseudo-json']

    def __init__(self, output_format):
        if output_format is None or output_format in Printer.formats:
            self.output_format = output_format
        else:
            raise ValueError(f'Invalid output format: {output_format} (valid options: {self.formats})')

    def pformat(self, content, *args, **kwargs):
        if isinstance(content, types.GeneratorType):
            return '\n'.join(self.pformat(c, *args, **kwargs) for c in content)
        elif self.output_format == 'json':
            return json.dumps(content, cls=PermissiveJSONEncoder, ensure_ascii=False)
        elif self.output_format == 'pseudo-json':
            return json.dumps(content, sort_keys=True, indent=4, cls=PseudoJsonEncoder, ensure_ascii=False)
        elif self.output_format == 'json-pretty':
            return json.dumps(content, sort_keys=True, indent=4, cls=PermissiveJSONEncoder, ensure_ascii=False)
        elif self.output_format == 'json-compact':
            return json.dumps(content, separators=(',', ':'), cls=PermissiveJSONEncoder, ensure_ascii=False)
        elif self.output_format == 'json-lines':
            if not isinstance(content, (list, set)):
                raise TypeError('Expected list or set; found {}'.format(type(content).__name__))
            lines = ['[']
            last = len(content) - 1
            for i, val in enumerate(content):
                suffix = ',' if i < last else ''
                lines.append(json.dumps(val, cls=PermissiveJSONEncoder, ensure_ascii=False) + suffix)
            lines.append(']\n')
            return '\n'.join(lines)
        elif self.output_format == 'plain':
            if isinstance(content, str):
                return content
            elif isinstance(content, Mapping):
                return '\n'.join('{}: {}'.format(k, v) for k, v in sorted(content.items()))
            elif all(isinstance(content, abc_type) for abc_type in (Sized, Iterable, Container)):
                return '\n'.join(sorted(map(str, content)))
            else:
                return str(content)
        elif self.output_format == 'yaml':
            return yaml_dump(content, kwargs.pop('force_single_yaml', False), kwargs.pop('indent_nested_lists', False))
        elif self.output_format == 'pprint':
            return pprint.pformat(content)
        else:
            return content

    def pprint(self, content, *args, gen_empty_error=None, **kwargs):
        if isinstance(content, types.GeneratorType):
            i = 0
            for c in content:
                self.pprint(c, *args, **kwargs)
                i += 1

            if (i == 0) and gen_empty_error:
                log.error(gen_empty_error)
        else:
            print(self.pformat(content, *args, **kwargs))


class PermissiveJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (set, KeysView)):
            return sorted(o)
        elif isinstance(o, ValuesView):
            return list(o)
        elif isinstance(o, Mapping):
            return dict(o)
        elif isinstance(o, bytes):
            return o.decode('utf-8')
        elif isinstance(o, datetime):
            return o.strftime('%Y-%m-%d %H:%M:%S %Z')
        elif isinstance(o, type):
            return str(o)
        elif hasattr(o, '__to_json__'):
            return o.__to_json__()
        elif hasattr(o, '__serializable__'):
            return o.__serializable__()
        return super().default(o)


class PseudoJsonEncoder(PermissiveJSONEncoder):
    def default(self, o):
        try:
            return super().default(o)
        except TypeError:
            return repr(o)


class IndentedYamlDumper(yaml.SafeDumper):
    """This indents lists that are nested in dicts in the same way as the Perl yaml library"""
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def prep_for_yaml(obj):
    if isinstance(obj, UserDict):
        obj = obj.data
    # noinspection PyTypeChecker
    if isinstance(obj, dict):
        return {prep_for_yaml(k): prep_for_yaml(v) for k, v in obj.items()}
    elif isinstance(obj, (list, set, tuple, map)):
        return [prep_for_yaml(v) for v in obj]
    else:
        return obj


def yaml_dump(data, force_single_yaml=False, indent_nested_lists=False, default_flow_style=None, **kwargs):
    """
    Serialize the given data as YAML

    :param data: Data structure to be serialized
    :param bool force_single_yaml: Force a single YAML document to be created instead of multiple ones when the
      top-level data structure is not a dict
    :param bool indent_nested_lists: Indent lists that are nested in dicts in the same way as the Perl yaml library
    :return str: Yaml-formatted data
    """
    content = prep_for_yaml(data)
    kwargs.setdefault('explicit_start', True)
    kwargs.setdefault('width', float('inf'))
    kwargs.setdefault('allow_unicode', True)
    if indent_nested_lists:
        kwargs['Dumper'] = IndentedYamlDumper

    if isinstance(content, (dict, str)) or force_single_yaml:
        kwargs.setdefault('default_flow_style', False if default_flow_style is None else default_flow_style)
        formatted = yaml.dump(content, **kwargs)
    else:
        kwargs.setdefault('default_flow_style', True if default_flow_style is None else default_flow_style)
        formatted = yaml.dump_all(content, **kwargs)
    if formatted.endswith('...\n'):
        formatted = formatted[:-4]
    if formatted.endswith('\n'):
        formatted = formatted[:-1]
    return formatted
