"""This module contains patched and alternative versions of pexpect objects."""

import os
import pty
import sys

import io
import termios

from pexpect import spawn
from pexpect.fdpexpect import fdspawn

from binexpect.mixins import BinMixin, PromptMixin


# Monkey patch spawn & fdspawn to add bin support.
spawn = type("spawn", (spawn, BinMixin, PromptMixin), {})
fdspawn = type("fdspawn", (fdspawn, BinMixin, PromptMixin), {})


class ttyspawn(fdspawn):  # NOQA: N801
    """
    Like pexpect.fdspawn but provides a new tty to work with.

    This is useful for example when interacting with programs running
    under gdb --tty=X self.master and self.slave contain the file
    descriptors for the created tty.  This class has not been tested
    on anything other than Linux & BSD.
    """

    def __init__(self, verbose=False, args=[], timeout=30,
                 maxread=2000, searchwindowsize=None, logfile=None):
        """
        Initialize a tty and setup the proxied pexpect.fdspawn isntance.

        Often a new tty is created to allow for interacton by another
        program, for those cases verbose can be set to True in order
        to have the tty's name be automatically printed to stderr. The
        other agruments are identical to those of fdspawn().
        """

        self.master, self.slave = pty.openpty()
        if verbose:
            sys.stderr.write("New tty spawned at %s\r\n" % self.ttyname())
        fdspawn.__init__(self, self.master, args, timeout,
                         maxread, searchwindowsize, logfile)

        readf = io.open(self.child_fd, 'rb', buffering=0)
        writef = io.open(self.child_fd, 'wb', buffering=0, closefd=False)
        self.fileobj = io.BufferedRWPair(readf, writef)
        try:
            from termios import VEOF, VINTR
            intr = ord(termios.tcgetattr(self.child_fd)[6][VINTR])
            eof = ord(termios.tcgetattr(self.child_fd)[6][VEOF])
        except (ImportError, OSError, IOError, ValueError, termios.error):
            # unless the controlling process is also not a terminal,
            # such as cron(1), or when stdin and stdout are both closed.
            # Fall-back to using CEOF and CINTR. There
            try:
                from termios import CEOF, CINTR
                (intr, eof) = (CINTR, CEOF)
            except ImportError:
                #             ^C, ^D
                (intr, eof) = (3, 4)

        self._INTR = bytes([intr])
        self._EOF = bytes([eof])

    def ttyname(self):
        """Return the name of the underlying TTY."""
        return os.ttyname(self.slave)

    def setecho(self, echo):
        """Mock setecho existance."""
        sys.stderr.write(
            "ttyspawn.setecho() is a no-op, echo is never fed back "
            "to pexpect but it is always kept on in the TTY.\r\n"
        )

    def sendeof(self):
        """Sends EOF to spawned tty"""
        self.fileobj.write(self._EOF)
        self.fileobj.flush()
