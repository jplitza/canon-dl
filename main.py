#!/usr/bin/python3

from operator import methodcaller
from collections.abc import Sequence
import argparse
import time
import os
import requests
import warnings

import gi
gi.require_version('GUPnP', '1.6')
gi.require_version('GUPnPAV', '1.0')
from gi.repository import GUPnP, GUPnPAV, GLib  # noqa: E402


class FileBackedList(Sequence):
    """Append-only list (of strings) that is backed by a file"""
    def __init__(self, path: str):
        try:
            self._fd = open(path, 'r+')
            self._data = list(
                line.rstrip("\r\n")
                for line in self._fd.readlines()
            )
        except FileNotFoundError:
            self._fd = open(path, 'x')
            self._data = list()

    def append(self, it: str):
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
    def __init__(
        self, basepath: str,
        interface: str = "eth0",
        daemon_mode: bool = False,
    ):
        self.basepath = basepath
        self.daemon_mode = daemon_mode
        self.con = GUPnP.Context.new_full(interface, None, 0, 0)

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

    def _download_file(self, item: GUPnPAV.DIDLLiteObject):
        try:
            date = time.strptime(item.get_date(), "%Y-%m-%dT%H:%M:%S")
        except TypeError:
            warnings.warn(
                "Could not determine date from for {}".format(
                    item,
                ),
                RuntimeWarning,
            )
            return

        # choose largest resource for download
        resource = sorted(
            item.get_resources(),
            key=methodcaller('get_size'),
        )[-1]

        destfile = os.path.join(
            time.strftime("%Y/%Y-%m-%d", date),
            item.get_title(),
        )
        full_destfile = os.path.join(
            self.basepath,
            destfile,
        )

        if destfile in self.previous:
            return

        try:
            counter = 0
            while os.stat(full_destfile).st_size != resource.get_size() and counter < 3:
                warnings.warn(
                    "File {} already exists with different size!".format(
                        destfile,
                    ),
                    RuntimeWarning,
                )
                full_destfile += '.retry'
                counter += 1
        except FileNotFoundError:
            pass

        os.makedirs(os.path.dirname(full_destfile), exist_ok=True)

        req = requests.get(resource.get_uri(), stream=True, timeout=10)
        if req.status_code != requests.codes.ok:
            warnings.warn(
                "File {} failed to download: HTTP code {}".format(
                    destfile,
                    req.status_code,
                ),
                RuntimeWarning,
            )
            return

        written_length = 0
        with open(full_destfile, 'wb') as fd:
            for chunk in req.iter_content(chunk_size=128):
                fd.write(chunk)
                written_length += len(chunk)

        if written_length != int(req.headers['Content-Length']):
            warnings.warn(
                "Download of {} aborted after {} bytes, deleting stub".format(
                    destfile,
                    written_length,
                ),
                RuntimeWarning,
            )
            os.unlink(full_destfile)
            return

        self.previous.append(destfile)

        if date:
            os.utime(full_destfile, times=(2 * (time.mktime(date),)))

    def _process_item(
        self,
        parser: GUPnPAV.DIDLLiteParser,
        item: GUPnPAV.DIDLLiteObject,
    ):
        if item.get_upnp_class().startswith("object.item.imageItem"):
            self._download_file(item)
        elif item.get_upnp_class().startswith("object.container"):
            self._fetch_all_items(item.get_id())

    def _fetch_all_items(self, parent: int = 0):
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
            action = GUPnP.ServiceProxyAction.new_from_list(
                "Browse",
                list(args.keys()),
                list(args.values()),
            )

            if self.service.call_action(action) is None:
                raise RuntimeError("Browse action failed")

            success, ret = action.get_result_list(
                ["Result", "NumberReturned", "TotalMatches"],
                [str, int, int],
            )

            if not success:
                raise RuntimeError("Browse action failed")

            total = ret[2]
            fetched += ret[1]
            parser.parse_didl(ret[0])

    def _device_found(self, cp: GUPnP.ControlPoint, device: GUPnP.Device):
        if device.get_model_description() != "Canon Digital Camera":
            return

        if not self.daemon_mode:
            self.loop.quit()

        self.service: GUPnP.ServiceInfo = device.get_service(
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
        '--daemon',
        help='keep listening for devices after download finished',
        action='store_true',
    )
    parser.add_argument(
        'basepath',
        help='folder where date-subfolders will be created',
    )
    args = parser.parse_args()
    downloader = CanonImageDownloader(args.basepath, args.ifname, args.daemon)
    downloader.run()
