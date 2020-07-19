#!/usr/bin/env python

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, PROJECT_ROOT.joinpath('bin').as_posix())
import _venv  # This will activate the venv, if it exists and is not already active

import logging
from argparse import ArgumentParser

# sys.path.insert(0, 'C:/Users/dougs/git/pymobiledevice')
sys.path.insert(0, PROJECT_ROOT.as_posix())
from pypod.__version__ import __author_email__, __version__
from pypod.shell import iDeviceShell


def parser():
    parser = ArgumentParser(description='iDevice Shell')
    parser.add_argument('--verbose', '-v', action='count', default=0, help='Increase logging verbosity (can specify multiple times)')
    return parser


def main():
    args = parser().parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(name)s %(lineno)d %(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(message)s')

    iDeviceShell().cmdloop()


if __name__ == '__main__':
    main()
