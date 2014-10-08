#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# The MIT License (MIT)

# Copyright (c) 2014 wapiflapi

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import sys
import pty
import signal

import argparse

import pexpect
import fdpexpect

from pexpect import spawn, EOF, TIMEOUT
from fdpexpect import fdspawn

# Why the fuck is this not already available in signal?
SIGNALS = dict((getattr(signal, n), n)
               for n in dir(signal) if n.startswith('SIG') and '_' not in n)


class binMixin(object):
    '''This MixIn adds support for raw binary comunications by escaping special
    characters in order to avoid TTY-controling sequences. This use the .send()
    and .sendline() methods of the base class.'''

    def escape(self, s):

        escaped = bytearray(len(s) * 2)

        for i, c in enumerate(s):
            escaped[i * 2] = 0x16
            escaped[i * 2 + 1] = c if isinstance(c, int) else ord(c)

        return bytes(escaped)

    def sendbin(self, s):
        return self.send(self.escape(s))

    def sendbinline(self, s=''):
        return self.sendline(self.escape(s))


class promptMixin(object):
    '''This MixIn allows to print a prompt when interacting with a target'''

    def prompt(self, prompt=None, escape_character=chr(29),
               input_filter=None, output_filter=None,
               echo=True,
               print_escape_character=True,
               exitwithprogram=True):
        '''Calls self.interact() after printing a prompt.'''

        if echo is not None:
            self.setecho(echo)

        if sys.stdout.isatty():
            if print_escape_character:
                sys.stdout.write("Escape character is '^%c'\r\n" % (
                        ord(escape_character) + 64))
            if prompt is not None:
                sys.stdout.write(prompt)

        self.interact(escape_character=escape_character,
                      input_filter=input_filter,
                      output_filter=output_filter)

        if self.isalive():
            return

        # Careful now, self might not have signal/exit status.
        if getattr(self, "signalstatus") is not None:
            sys.stdout.write("Program received signal %d. (%s)\r\n" % (
                    self.signalstatus,
                    SIGNALS.get(self.signalstatus, "Unknown")))
            if exitwithprogram:
                sys.stdout.write("Killing ourself with same signal.\r\n")
                os.kill(os.getpid(), self.signalstatus)
        elif getattr(self, "exitstatus") is not None:
            sys.stdout.write("Program exited with status %d.\r\n" % (
                    self.exitstatus))
            if exitwithprogram:
                sys.stdout.write("Exiting with same status.\r\n")
                exit(self.exitstatus)


    def tryexpect(self, pattern, timeout=None, searchwindowsize=None,
                  exitwithprogram=True):
        '''
        Proxy for expect. Prompts when an expected pattern wasn't received
        before timeout. If EOF is raised by pexpect the status of the target
        is checked and if it received a signal or exited it is mentioned.
        If the exitwithprogram argument is not passed as False, tryexpect
        will do its best to terminate itself in the same way as the target.
        '''
        try:
            return self.expect(pattern, timeout, searchwindowsize)
        except TIMEOUT:
            self.prompt("Didn't receive expected %r.\r\n" % pattern)
            sys.stdout.write("Continuing script.\r\n")
        except EOF:
            if self.isalive():
                raise
            # Careful now, self might not have signal/exit status.
            if getattr(self, "signalstatus") is not None:
                sys.stdout.write("Program received signal %d. (%s)\r\n" % (
                        self.signalstatus,
                        SIGNALS.get(self.signalstatus, "Unknown")))
                if exitwithprogram:
                    sys.stdout.write("Killing ourself with same signal.\r\n")
                    os.kill(os.getpid(), self.signalstatus)
            elif getattr(self, "exitstatus") is not None:
                sys.stdout.write("Program exited with status %d.\r\n" % (
                        self.exitstatus))
                if exitwithprogram:
                    sys.stdout.write("Exiting with same status.\r\n")
                    exit(self.exitstatus)
            else:
                raise


# Monkey patch spawn & fdspawn to add bin support.
spawn = type("spawn", (spawn, binMixin, promptMixin), {})
fdspawn = type("fdspawn", (fdspawn, binMixin, promptMixin), {})


class ttyspawn(fdspawn):
    '''This is like pexpect.fdspawn, it provides a new tty to work with. This is
    useful for example when interacting with programs running under gdb --tty=X
    self.master and self.slave contain the file descriptors for the created tty.
    This class has not been tested on anything other than Linux & BSD.'''

    # Ok, I have no idea why this would take args, but hey lets just proxy this shit.
    def __init__(self, verbose=False, args=[], timeout=30,
                 maxread=2000, searchwindowsize=None, logfile=None):
        '''Often a new tty is created to allow for interacton by another program,
        for those cases verbose can be set to True in order to have the tty's name
        be automatically printed to stderr. The other agruments are identical to
        those of fdspawn().'''

        self.master, self.slave = pty.openpty()
        if verbose:
            sys.stderr.write("New tty spawned at %s\r\n" % self.ttyname())
        fdspawn.__init__(self, self.master, args, timeout,
                         maxread, searchwindowsize, logfile)

    def ttyname(self):
        return os.ttyname(self.slave)


class setup(object):
    '''
    This helper class uses argparse to setup a sensible CLI to facilitate tests.
    It can be used to switch between calling a program directly or setting up a
    TTY, or to pass options to pexepect.
    argparse's parser is available through the .parser attribute and you can add
    any options you want before calling .target(), After the call to .taarget()
    arguments will we available in .args.
    '''

    def __init__(self, command=None, args=[], timeout=30, maxread=2000,
                 searchwindowsize=None, logfile=None, cwd=None, env=None,
                 ignore_sighup=True):

        self.parser = argparse.ArgumentParser()

        options = self.parser.add_argument_group('binexpect options')

        options.add_argument("-t", "--tty", action="store_true",
                             help="Spawn a new TTY and interact with it "
                             "instead of spawning the process.")
        options.add_argument("-q", "--quiet", dest="verbose", action="store_false",
                             help="Don't print information such as the TTY's name.")

        options.set_defaults(command=command, args=args)
        options.add_argument("--timeout", type=int, default=timeout,
                             help="If an expected message isn't received in TIMEOUT seconds "
                             "the target program will be considered terminated.")
        options.add_argument("--maxread", type=int, default=maxread,
                             help="This sets the read buffer size. This is the maximum number "
                             "of bytes that Pexpect will try to read from a TTY at one time. "
                             "Setting the maxread size to 1 will turn off buffering.")
        options.add_argument("--search-window-size", type=int, default=searchwindowsize,
                             help="This sets how far back in the incoming search buffer "
                             "pexpect will search for pattern matches.")
        options.add_argument("--logfile", type=argparse.FileType("w"), default=logfile,
                             help="Pexpect will be asked to copy all input and output"
                             "to the given file.")
        options.add_argument("--cwd", default=cwd,
                             help="Sets the child process' current working directory.")
        options.add_argument("--env", default=env,
                             help="Sets the child process' environement.")
        options.add_argument("--ignore-sighup", action="store_true", default=ignore_sighup,
                             help="If set, this option will cause the child process will "
                             "ignore SIGHUP signals.")

    def target(self, *args):

        # Those options must be added last because they take the remaining arguments.
        self.parser.add_argument("command", nargs="?")
        self.parser.add_argument("args", nargs=argparse.REMAINDER)

        self.args = self.parser.parse_args(*args)

        if self.args.tty:
            return ttyspawn(verbose=self.args.verbose, args=self.args.args,
                            timeout=self.args.timeout, maxread=self.args.maxread,
                            searchwindowsize=self.args.search_window_size,
                            logfile=self.args.logfile)
        else:
            return spawn(command=self.args.command, args=self.args.args,
                         timeout=self.args.timeout, maxread=self.args.maxread,
                         searchwindowsize=self.args.search_window_size,
                         logfile=self.args.logfile, cwd=self.args.cwd,
                         env=self.args.env, ignore_sighup=self.args.ignore_sighup)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="""
This is a python module that monkeypatches pexpect and adds support
for raw binary comunications by escaping special characters in order
to avoid TTY-controling sequences. This command line interfaces spawns
a new TTY to which other programs can attach, for example gdb --tty=X.
This is really intended to be used as a module not as CLI.""",
                                     epilog="""
Written by wapiflapi@yahoo.fr, please feel free to send any comments or
bug-reports you might have. Hosted on github.com/wapiflapi/binexpect""")

    parser.add_argument("--logfile", "-l", metavar="FILE",
                        type=argparse.FileType("wb"), default=None,
                        help="Acts as script and makes a copy of "
                        "everything printed to the terminal.")
    args = parser.parse_args()

    tty = ttyspawn(verbose=True, logfile=args.logfile)

    tty.prompt()