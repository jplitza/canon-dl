Canon Image Downloader
======================

This script downloads images from a network connected Canon DSLR camera in UPnP
mode into date-based folders.

Requirements
------------

* [requests](https://pypi.org/project/requests/)
* [PyGObject](https://pygobject.readthedocs.io/en/latest/),
  with introspection data for
  * GUPnP
  * GUPnP-AV
  * GLib

Usage
-----
```
usage: main.py [-h] [--ifname IFNAME] basepath

Download images from a Canon DSLR

positional arguments:
  basepath         folder where date-subfolders will be created

optional arguments:
  -h, --help       show this help message and exit
  --ifname IFNAME  name of network interface to search on
```
