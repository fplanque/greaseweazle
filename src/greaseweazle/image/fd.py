# greaseweazle/image/fd.py
#
# Written & released by Keir Fraser <keir.xen@gmail.com>
#
# This is free and unencumbered software released into the public domain.
# See the file COPYING for more details, or visit <http://unlicense.org>.

from greaseweazle.image.img import IMG

class FD(IMG):
    default_format = 'thomson.1s80'
    sequential = True

# Local variables:
# python-indent: 4
# End:
