PyPod
=====

Originally forked from `iOSForensics/pymobiledevice <https://github.com/iOSForensics/pymobiledevice>`_.

Due to very heavy modifications, including the deletion of a large amount of code and the addition of new functionality,
it made more sense to create a new repo.


Summary
-------

Provides a more object-oriented approach to working with iDevices:

* iDevice: Represents an iPod or other supported iDevice.  Provides methods for retrieving iPaths and information.
* iPath: A subclass of pathlib.Path for working with paths on iDevices
* iPodIOBase: Wraps AFC calls for working with files on iDevices
* iTextIOWrapper: TextIO wrapper for text files

Additionally, a minimal / basic BASH clone was implemented with prompt_toolkit (an optional dependency) to explore
files.  It allows copying of files to/from the iDevice.


Installation
------------

If installing on Linux, you should run the following first::

    $ sudo apt-get install python3-dev


Regardless of OS, setuptools is required::

    $ pip3 install setuptools


All of the other requirements are handled in setup.py, which will be run when you install like this::

    $ pip3 install git+git://github.com/dskrypa/pypod

