"""This module contains patched and alternative versions of pexpect objects."""

import os
import pty
import tty
import sys
import termios

from pexpect import spawn
from pexpect.fdpexpect import fdspawn
from pexpect.utils import poll_ignore_interrupts, select_ignore_interrupts

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

        self.STDIN_FILENO = pty.STDIN_FILENO
        self.STDOUT_FILENO = pty.STDOUT_FILENO

        # get the EOF and INTR char
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
        self.send(self._EOF)


    def interact(self, escape_character=chr(29),
            input_filter=None, output_filter=None):
        """This methods transfers STDIN and STDOUT to the spawned tty"""

        self.write_to_stdout(self.buffer)
        self.stdout.flush()
        self._buffer = self.buffer_type()
        mode = tty.tcgetattr(self.STDIN_FILENO)
        tty.setraw(self.STDIN_FILENO)

        if escape_character is not None:
            escape_character = escape_character.encode('latin-1')
        try:
            self.__interact_copy(escape_character, input_filter, output_filter)
        finally:
            tty.tcsetattr(self.STDIN_FILENO, tty.TCSAFLUSH, mode)

    def __interact_writen(self, fd, data):
        '''This is used by the interact() method.
        '''

        while data != b'' and self.isalive():
            n = os.write(fd, data)
            data = data[n:]

    def __interact_read(self, fd):
        '''This is used by the interact() method.
        '''

        return os.read(fd, 1000)

    def __interact_copy(self, escape_character=None,
            input_filter=None, output_filter=None):
        '''This is used by the interact() method.
        '''

        while self.isalive():
            if self.use_poll:
                r = poll_ignore_interrupts([self.child_fd, self.STDIN_FILENO])
            else:
                r, w, e = select_ignore_interrupts(
                    [self.child_fd, self.STDIN_FILENO], [], []
                )
            if self.child_fd in r:
                try:
                    data = self.__interact_read(self.child_fd)
                except OSError as err:
                    if err.args[0] == errno.EIO:
                        # Linux-style EOF
                        break
                    raise
                if data == b'':
                    # BSD-style EOF
                    break
                if output_filter:
                    data = output_filter(data)
                self._log(data, 'read')
                os.write(self.STDOUT_FILENO, data)
            if self.STDIN_FILENO in r:
                data = self.__interact_read(self.STDIN_FILENO)
                if input_filter:
                    data = input_filter(data)
                i = -1
                if escape_character is not None:
                    i = data.rfind(escape_character)
                if i != -1:
                    self.__interact_writen(self.child_fd, data[:i])
                    break
                self.__interact_writen(self.child_fd, data)
