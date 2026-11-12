"""Per-branch QEMU virtual machines with overlay rollback.

Each exploration branch gets its own VM: a qcow2 overlay is created on top of
a prepared base image, QEMU boots it under KVM, commands run over the serial
console, and the branch ends by killing QEMU and deleting the overlay. That
gives the paper's isolation property (a branch cannot see or affect another
branch's disk state) and its rollback property (the next branch starts from
the same base, at the cost of a ~200 KiB overlay instead of a 20 GiB copy).

This module does not pretend to be a security sandbox: the VM is disposable
and local, and the base image is operator-supplied. The control channel is an
autologin root shell on the serial console, which is fine for a throwaway
experiment VM and must not be used for anything else.

Requirements on the prepared base image (see log/vm_prepare.sh):
  - kernel command line contains console=ttyS0,115200n8
  - serial-getty@ttyS0 is enabled with autologin enabled
"""
import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_END = "__RSI_END__"


_BRANCHES = set()
_REGISTRY_LOCK = threading.Lock()
_HOOKS_INSTALLED = False


def _close_all_branches(*_args):
    """Best-effort teardown for every live branch (atexit / signal)."""
    with _REGISTRY_LOCK:
        branches = list(_BRANCHES)
    for branch in branches:
        try:
            branch.close()
        except Exception:
            pass


def _install_hooks():
    """Kill branch VMs when the interpreter exits or is asked to stop.

    ``timeout`` and Ctrl-C deliver SIGTERM/SIGINT; without this the QEMU
    process outlives the Python driver and keeps the overlay open.
    """
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    _HOOKS_INSTALLED = True
    atexit.register(_close_all_branches)
    if threading.current_thread() is not threading.main_thread():
        return

    def _handler(signum, frame):
        _close_all_branches()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


def _socket_is_live(path):
    """A console socket with no listener is a leftover, not a live branch."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        probe.close()


def cleanup_stale_dirs(prefix="rsi-vm-", root="/tmp"):
    """Remove overlay directories left behind by a killed driver."""
    removed = 0
    for name in os.listdir(root):
        if not name.startswith(prefix):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        sock_path = os.path.join(path, "console.sock")
        if os.path.exists(sock_path) and _socket_is_live(sock_path):
            continue  # a live branch owns it
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


class VMError(RuntimeError):
    """The branch VM could not be started or lost its console."""


def _strip(text):
    return _ANSI.sub("", text)


class VMBranch:
    """One disposable VM: overlay disk + serial console + command runner."""

    def __init__(self, base_image, *, memory_mb=2048, cpus=2, boot_timeout=180,
                 command_timeout=60, qemu="qemu-system-x86_64", accel="kvm",
                 sudo=False, keep_overlay=False):
        if not os.path.isfile(base_image):
            raise VMError("base image not found: " + str(base_image))
        self.base_image = os.path.abspath(base_image)
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.boot_timeout = boot_timeout
        self.command_timeout = command_timeout
        self.qemu = qemu
        self.accel = accel
        self.sudo = sudo
        self.keep_overlay = keep_overlay
        self.dir = None
        self.overlay = None
        self.socket_path = None
        self.proc = None
        self.sock = None
        self.buffer = ""

    # --- lifecycle -------------------------------------------------------
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def start(self):
        self.dir = tempfile.mkdtemp(prefix="rsi-vm-")
        self.overlay = os.path.join(self.dir, "disk.qcow2")
        self.socket_path = os.path.join(self.dir, "console.sock")
        subprocess.run([self.qemu.replace("qemu-system-x86_64", "qemu-img"),
                        "create", "-f", "qcow2", "-b", self.base_image,
                        "-F", "qcow2", self.overlay],
                       check=True, capture_output=True, text=True)
        argv = [
            self.qemu, "-accel", self.accel, "-cpu", "host",
            "-smp", str(self.cpus), "-m", str(self.memory_mb),
            "-drive", "file={},if=virtio,format=qcow2".format(self.overlay),
            "-display", "none", "-monitor", "none", "-no-reboot",
            "-chardev", "socket,id=con,path={},server=on,wait=off"
                        .format(self.socket_path),
            "-serial", "chardev:con",
        ]
        if self.sudo:
            argv = ["sudo", "-n"] + argv
        _install_hooks()
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        with _REGISTRY_LOCK:
            _BRANCHES.add(self)
        self._connect()
        self._wait_for_shell()
        return self

    def _connect(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(self.socket_path):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    sock.connect(self.socket_path)
                except OSError:
                    sock.close()
                    time.sleep(0.2)
                    continue
                sock.settimeout(0.5)
                self.sock = sock
                return
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode("utf-8", "replace")
                raise VMError("qemu exited before the console was ready: " + err[:400])
            time.sleep(0.2)
        raise VMError("console socket never appeared: " + self.socket_path)

    def _read_until(self, marker, timeout):
        deadline = time.time() + timeout
        while marker not in self.buffer:
            if time.time() > deadline:
                raise VMError("timed out waiting for " + marker +
                              "; recent console: " + self.buffer[-300:])
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                if self.proc.poll() is not None:
                    raise VMError("qemu exited while waiting for " + marker)
                continue
            if not chunk:
                raise VMError("console closed while waiting for " + marker)
            self.buffer += chunk.decode("utf-8", "replace")
        head, _, self.buffer = self.buffer.partition(marker)
        return head

    def _wait_for_shell(self):
        # The autologin shell prints a root prompt; nudge it once.
        deadline = time.time() + self.boot_timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
                self.buffer += chunk.decode("utf-8", "replace")
            except socket.timeout:
                self._send("\n")
                continue
            if re.search(r"root@[^\n]*[#$]", _strip(self.buffer)):
                self._send("stty -echo; export TERM=dumb; PS1=''; PROMPT_COMMAND=''\n")
                self._drain()
                self.buffer = ""
                return
            if self.proc.poll() is not None:
                raise VMError("qemu exited during boot")
        raise VMError("no root shell on the serial console within "
                      + str(self.boot_timeout) + "s; console tail: "
                      + _strip(self.buffer)[-300:])

    def _send(self, text):
        self.sock.sendall(text.encode())

    def _drain(self, quiet=0.4, limit=20.0):
        """Read and discard console output until it goes quiet."""
        self.sock.settimeout(quiet)
        deadline = time.time() + limit
        try:
            while time.time() < deadline:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    return
                if not chunk:
                    return
        finally:
            self.sock.settimeout(0.5)

    # --- work -------------------------------------------------------------
    def run(self, command, timeout=None):
        """Run one shell command; return (exit_code, cleaned_output)."""
        timeout = timeout or self.command_timeout
        marker = _END + str(int(time.time() * 1000) % 1000000)
        self.buffer = ""
        self._send("{ " + command + "; } 2>&1; __rsi_rc=$?; "
                   "echo \"__RSI_RC__$__rsi_rc\"; echo \"" + marker + "\"\n")
        raw = _strip(self._read_until(marker, timeout))
        match = re.search(r"__RSI_RC__(-?\d+)", raw)
        code = int(match.group(1)) if match else -1
        text = raw[:match.start()] if match else raw
        text = text.replace("__RSI_RC__", "")
        lines = text.splitlines()
        # The first line is the echoed command; drop it when it matches.
        if lines and command.strip() and command.strip() in lines[0]:
            lines = lines[1:]
        return code, "\n".join(lines).strip("\n")

    def close(self):
        with _REGISTRY_LOCK:
            _BRANCHES.discard(self)
        if getattr(self, "_closed", False):
            return
        self._closed = True
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.proc is not None and self.proc.stderr is not None:
            try:
                self.proc.stderr.close()
            except OSError:
                pass
        self.proc = None
        if self.dir is not None and not self.keep_overlay:
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir = None

    @property
    def overlay_bytes(self):
        if self.overlay and os.path.exists(self.overlay):
            return os.path.getsize(self.overlay)
        return 0
