#!/usr/bin/python3

from operator import methodcaller
from collections.abc import Sequence
import argparse
import time
import os
import requests
import warnings

import gi
gi.require_version('GUPnP', '1.0')
gi.require_version('GUPnPAV', '1.0')
from gi.repository import GUPnP, GUPnPAV, GLib  # noqa: E402


class FileBackedList(Sequence):
    """Append-only list (of strings) that is backed by a file"""
    def __init__(self, path):
        try:
            self._fd = open(path, 'r+')
            self._data = list(
                line.rstrip("".join(self._fd.newlines))
                for line in self._fd.readlines()
            )
        except FileNotFoundError:
            self._fd = open(path, 'x')
            self._data = list()

    def append(self, it):
        self._data.append(it)
        self._fd.write(it)
        self._fd.write('\n')
        self._fd.flush()

    def __len__(self):
        return len(self._data)

    def __getitem__(self, i):
        return self._data[i]

    def __repr__(self):
        return repr(self._data)

    def __del__(self):
        self._fd.close()


class CanonImageDownloader:
    def __init__(self, basepath, interface="eth0"):
        self.basepath = basepath
        self.con = GUPnP.Context.new(None, interface, 0)

        self.cp = GUPnP.ControlPoint.new(
            context=self.con,
            target="urn:schemas-upnp-org:device:MediaServer:1",
        )
        self.cp.connect("device-proxy-available", self._device_found)
        self.cp.set_active(True)

        self.loop = GLib.MainLoop()

        self.previous = FileBackedList(os.path.join(basepath, '.sync-state'))

    def run(self):
        self.loop.run()

    def _download_file(self, item):
        try:
            date = time.strptime(item.get_date(), "%Y-%m-%dT%H:%M:%S")
        except TypeError:
            date = None

        # choose largest resource for download
        resource = sorted(
            item.get_resources(),
            key=methodcaller('get_size'),
        )[-1]

        destfile = os.path.join(
            time.strftime("%Y-%m-%d", date) if date else "0000-00-00",
            item.get_title(),
        )
        full_destfile = os.path.join(
            self.basepath,
            destfile,
        )

        if destfile in self.previous:
            return

        try:
            if os.stat(full_destfile).st_size != resource.get_size():
                warnings.warn(
                    "File {} already exists with different size!".format(
                        destfile,
                    ),
                    RuntimeWarning,
                )
                return
        except FileNotFoundError:
            pass

        os.makedirs(os.path.dirname(full_destfile), exist_ok=True)

        req = requests.get(resource.get_uri(), stream=True)
        with open(full_destfile, 'wb') as fd:
            for chunk in req.iter_content(chunk_size=128):
                fd.write(chunk)

        self.previous.append(destfile)

        if date:
            os.utime(full_destfile, times=(2 * (time.mktime(date),)))

    def _process_item(self, parser, item):
        if item.get_upnp_class().startswith("object.item.imageItem"):
            self._download_file(item)
        elif item.get_upnp_class().startswith("object.container"):
            self._fetch_all_items(item.get_id())

    def _fetch_all_items(self, parent=0):
        total = None
        fetched = 0

        parser = GUPnPAV.DIDLLiteParser()
        parser.connect("object-available", self._process_item)

        while total is None or fetched < total:
            # Canons UPnP implementation seems to require that we send all
            # arguments, even if they are the default values
            args = {
                "ObjectID": parent,
                "BrowseFlag": "BrowseDirectChildren",
                "Filter": "dc:title,dc:date,res@size",
                "SortCriteria": "",
                "StartingIndex": fetched,
                "RequestedCount": 0,
            }
            ret = self.service.send_action_list(
                "Browse",
                list(args.keys()),
                list(args.values()),
                ["Result", "NumberReturned", "TotalMatches"],
                [str, int, int],
            )

            if not ret[0]:
                raise RuntimeError("Browse action failed")

            total = ret[1][2]
            fetched += ret[1][1]
            parser.parse_didl(ret[1][0])

    def _device_found(self, cp, device):
        if device.get_model_description() != "Canon Digital Camera":
            return
        self.loop.quit()

        self.service = device.get_service(
            "urn:schemas-upnp-org:service:ContentDirectory:1"
        )
        self._fetch_all_items()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Download images from a Canon DSLR',
    )
    parser.add_argument(
        '--ifname',
        help='name of network interface to search on',
        default='eth0',
    )
    parser.add_argument(
        'basepath',
        help='folder where date-subfolders will be created',
    )
    args = parser.parse_args()
    downloader = CanonImageDownloader(args.basepath, args.ifname)
    downloader.run()