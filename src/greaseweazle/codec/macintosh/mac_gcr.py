# greaseweazle/codec/macintosh/mac_gcr.py
#
# Written & released by Keir Fraser <keir.xen@gmail.com>
#
# This is free and unencumbered software released into the public domain.
# See the file COPYING for more details, or visit <http://unlicense.org>.

from typing import List, Optional, Tuple

import struct
import itertools as it
from bitarray import bitarray

from greaseweazle import error
from greaseweazle import optimised
from greaseweazle.codec.ibm import ibm
from greaseweazle.track import MasterTrack, RawTrack
from greaseweazle.flux import Flux

default_revs = 1.1

self_sync_bytes = b'\xff\x3f\xcf\xf3\xfc\xff'
sector_sync_bytes = b'\xd5\xaa\x96' # 1101 0101 1010 1010 1001 0110
data_sync_bytes   = b'\xd5\xaa\xad' # 1101 0101 1010 1010 1010 1101

sector_sync = bitarray(endian='big')
sector_sync.frombytes(sector_sync_bytes)

data_sync = bitarray(endian='big')
data_sync.frombytes(data_sync_bytes)

seclen = 524
enc_seclen = 703
format_byte = 0x22

bad_sector = b'-=[BAD SECTOR]=-' * 32

class MacGCR:

    time_per_rev = 0.2

    def __init__(self, cyl: int, head: int, config):
        self.cyl, self.head = cyl, head
        self.config = config
        self.nsec = config.secs
        self.clock = config.clock
        self.sector: List[Optional[bytes]]
        self.sector = [None] * self.nsec
        sec_map, pos = [-1] * self.nsec, 0
        for i in range(self.nsec):
            while sec_map[pos] != -1:
                pos = (pos + 1) % self.nsec
            sec_map[pos] = i
            pos = (pos + config.interleave) % self.nsec
        self.sec_map = sec_map

    def summary_string(self) -> str:
        nsec, nbad = self.nsec, self.nr_missing()
        s = "Macintosh GCR (%d/%d sectors)" % (nsec - nbad, nsec)
        return s

    # private
    def exists(self, sec_id) -> bool:
        return self.sector[sec_id] is not None

    # private
    def add(self, sec_id, data) -> None:
        assert not self.exists(sec_id)
        self.sector[sec_id] = data

    def has_sec(self, sec_id) -> bool:
        return self.sector[sec_id] is not None

    def nr_missing(self) -> int:
        return len([sec for sec in self.sector if sec is None])

    def get_img_track(self) -> bytearray:
        tdat = bytearray()
        for sec in self.sector:
            tdat += sec[12:] if sec is not None else bad_sector
        return tdat

    def set_img_track(self, tdat: bytearray) -> int:
        totsize = self.nsec * 512
        if len(tdat) < totsize:
            tdat += bytes(totsize - len(tdat))
        for sec in range(self.nsec):
            self.sector[sec] = bytes(12) + tdat[sec*512:(sec+1)*512]
        return totsize

    def flux(self, *args, **kwargs) -> Flux:
        return self.raw_track().flux(*args, **kwargs)


    def decode_raw(self, track, pll=None) -> None:
        raw = RawTrack(time_per_rev = self.time_per_rev,
                       clock = self.clock, data = track, pll = pll)
        bits, _ = raw.get_all_data()

        for offs in bits.itersearch(sector_sync):

            if self.nr_missing() == 0:
                break

            # Decode header
            offs += 3*8
            sec = bits[offs:offs+5*8].tobytes()
            if len(sec) != 5:
                continue
            hdr = optimised.decode_mac_gcr(sec)
            sum = 0
            for x in hdr:
                sum ^= x
            if sum != 0:
                continue
            cyl, sec_id, side, fmt = tuple(hdr[:4])
            cyl |= (side & 1) << 6
            side >>= 5
            if (cyl != self.cyl or side != self.head or sec_id >= self.nsec
                or fmt != 0x22):
                print('T%d.%d: Ignoring unexpected sector '
                      'C:%d H:%d R:%d F:0x%x'
                      % (self.cyl, self.head, cyl, side, sec_id, fmt))

            # Find data
            offs += 5*8
            dat_offs = bits[offs:offs+100*8].search(data_sync)
            if len(dat_offs) != 1:
                continue
            offs += dat_offs[0]

            # Decode data
            offs += 4*8
            sec = bits[offs:offs+703*8].tobytes()
            if len(sec) != 703:
                continue
            sec = optimised.decode_mac_gcr(sec)
            sec, csum = optimised.decode_mac_sector(sec)
            if csum != 0:
                continue

            self.add(sec_id, sec)


    def raw_track(self) -> MasterTrack:

        # Post-index track gap.
        t = bytes([0x96] * 64) + self_sync_bytes * 20

        for nr, sec_id in enumerate(self.sec_map):
            sector = self.sector[sec_id]
            data = (bytes(12) + bad_sector) if sector is None else sector
            cyl, side = self.cyl, self.head
            side = (side << 5) | (cyl >> 6)
            cyl &= 0x3f
            hdr = bytes([cyl, sec_id, side, 0x22])
            sum = 0
            for x in hdr:
                sum ^= x
            t += self_sync_bytes * 6
            t += sector_sync_bytes
            t += optimised.encode_mac_gcr(hdr + bytes([sum]))
            t += b'\xde\xaa\xff\xff'
            t += self_sync_bytes
            t += data_sync_bytes
            t += optimised.encode_mac_gcr(bytes([sec_id]))
            t += optimised.encode_mac_gcr(optimised.encode_mac_sector(data))
            t += b'\xde\xaa\xff'

        # Add the pre-index gap.
        t += b'\xff' * 4
        tlen = int((self.time_per_rev / self.clock)) & ~31
        t += bytes([0x96] * (tlen//8-len(t)))

        track = MasterTrack(bits = t, time_per_rev = 0.2)
        track.verify = self
        track.verify_revs = default_revs
        return track


    def verify_track(self, flux):
        readback_track = self.__class__(self.cyl, self.head, self.config)
        return (readback_track.nr_missing() == 0
                and self.sector == readback_track.sector)


class MacGCRTrackFormat:

    default_revs = default_revs

    def __init__(self, format_name: str):
        self.secs: Optional[int] = None
        self.clock: Optional[float] = None
        self.interleave = 1
        self.finalised = False

    def add_param(self, key: str, val) -> None:
        if key == 'secs':
            val = int(val)
            self.secs = val
        elif key == 'clock':
            val = float(val)
            self.clock = val * 1e-6
        elif key == 'interleave':
            val = int(val)
            self.interleave = val
        else:
            raise error.Fatal('unrecognised track option %s' % key)

    def finalise(self) -> None:
        if self.finalised:
            return
        error.check(self.secs is not None,
                    'number of sectors not specified')
        error.check(self.clock is not None,
                    'clock period not specified')
        self.finalised = True

    def mk_track(self, cyl: int, head: int) -> MacGCR:
        return MacGCR(cyl, head, self)


# Local variables:
# python-indent: 4
# End:
