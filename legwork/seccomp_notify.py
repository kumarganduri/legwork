"""What a served tool tried to reach, on Linux (issue #5).

Linux has no sandbox log to watch, as macOS has. A served tool without
network runs in an empty network namespace, so its connections fail with
nothing recorded anywhere. This uses seccomp user notification: a few lines
run inside the sandbox before the tool (LOADER) install a filter that pauses
every connect() and hands the filter's listener to the hub, over a socket
the hub made. The hub, outside the sandbox, reads each paused call's
destination, records it, and lets the call continue: the empty namespace
refuses it exactly as before, so the tool behaves the same, and nothing it
does can reach the listener. Unlike macOS, the destination isn't hidden.

Best effort: it needs Linux 5.5+ on x86-64 or ARM64, and a served command
that starts with Python (every built wrapper does).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fcntl
import ipaddress
import os
import platform
import select
import socket
import struct

# machine -> (AUDIT_ARCH value, connect syscall number, seccomp syscall number)
ARCHES = {
    "x86_64": (0xC000003E, 42, 317),
    "amd64": (0xC000003E, 42, 317),
    "aarch64": (0xC00000B7, 203, 277),
    "arm64": (0xC00000B7, 203, 277),
}
FD_ENV = "LEGWORK_NOTIFY_FD"  # the socket `legwork serve` passes into the sandbox

_RET_ALLOW, _RET_USER_NOTIF = 0x7FFF0000, 0x7FC00000
_LD_W_ABS, _JEQ_K, _RET_K = 0x20, 0x15, 0x06
# struct seccomp_notif is 80 bytes and seccomp_notif_resp 24; the ioctl
# numbers encode those sizes (_IOWR('!', 0 / 1, size)).
_NOTIF = struct.Struct("=QII iIQ6Q")
_RESP = struct.Struct("=QqiI")
_IOCTL_RECV = 0xC0000000 | (_NOTIF.size << 16) | (0x21 << 8) | 0
_IOCTL_SEND = 0xC0000000 | (_RESP.size << 16) | (0x21 << 8) | 1
_FLAG_CONTINUE = 1
_AF_INET, _AF_INET6 = 2, 10


def filter_program(machine: str | None = None) -> bytes | None:
    """The BPF program: connect() pauses for the listener, everything else
    is left to the sandbox's other rules. None on other architectures."""
    arch = ARCHES.get((machine or platform.machine()).lower())
    if arch is None:
        return None
    audit_arch, connect_nr, _ = arch

    def op(code: int, jt: int, jf: int, k: int) -> bytes:
        return struct.pack("<HBBI", code, jt, jf, k)

    return b"".join([
        op(_LD_W_ABS, 0, 0, 4),  # 0: A = arch
        op(_JEQ_K, 0, 3, audit_arch),  # 1: native ? 2 : 5
        op(_LD_W_ABS, 0, 0, 0),  # 2: A = syscall number
        op(_JEQ_K, 0, 1, connect_nr),  # 3: connect ? 4 : 5
        op(_RET_K, 0, 0, _RET_USER_NOTIF),  # 4: pause for the hub
        op(_RET_K, 0, 0, _RET_ALLOW),  # 5: not this filter's business
    ])


# Runs inside the sandbox, with the tool's own Python, before the tool:
# `python -I -c LOADER <socket fd> <program bytes, hex> <seccomp nr> <command...>`.
# Standard library only, and it never fails the tool: if the kernel can't do
# it, it says so and runs the tool as it would have run anyway.
LOADER = r"""
import ctypes, os, socket, struct, sys
sock_fd, program, seccomp_nr = int(sys.argv[1]), bytes.fromhex(sys.argv[2]), int(sys.argv[3])
command = sys.argv[4:]
sock = socket.socket(fileno=sock_fd)
try:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS: bwrap sets it already; filters need it
    code = ctypes.create_string_buffer(program, len(program))
    class Fprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]
    fprog = Fprog(len(program) // 8, ctypes.addressof(code))
    # SECCOMP_SET_MODE_FILTER with SECCOMP_FILTER_FLAG_NEW_LISTENER
    listener = libc.syscall(ctypes.c_long(seccomp_nr), ctypes.c_long(1), ctypes.c_long(8), ctypes.byref(fprog))
    if listener < 0:
        sock.sendall(b"N" + os.strerror(ctypes.get_errno()).encode()[:200])
    else:
        sock.sendmsg([b"L"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", listener))])
        os.close(listener)
except Exception as exc:
    try:
        sock.sendall(b"N" + repr(exc).encode()[:200])
    except OSError:
        pass
sock.close()
os.execv(command[0], command)
"""


def wrap(command: list[str], sock_fd: int, machine: str | None = None) -> list[str] | None:
    """`command`, started through LOADER, or None when it can't be (another
    architecture, or a command that isn't started with Python)."""
    arch = ARCHES.get((machine or platform.machine()).lower())
    program = filter_program(machine)
    if arch is None or program is None or not os.path.basename(command[0]).startswith("python"):
        return None
    return [command[0], "-I", "-c", LOADER, str(sock_fd), program.hex(), str(arch[2]), *command]


def receive_listener(sock: socket.socket, timeout: float = 30.0) -> int | None:
    """The listener fd LOADER sent, or None if it couldn't make one."""
    sock.settimeout(timeout)
    try:
        data, ancillary, _, _ = sock.recvmsg(256, socket.CMSG_SPACE(struct.calcsize("i")))
    except OSError:
        return None
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS and len(payload) >= 4:
            return struct.unpack("i", payload[:4])[0]
    return None


def describe(family: int, raw: bytes) -> str | None:
    """A paused connect() in words, or None for what isn't an attempt to
    leave the sandbox (a Unix socket, the tool's own loopback services)."""
    try:
        if family == _AF_INET and len(raw) >= 8:
            port, address = struct.unpack("!H", raw[2:4])[0], ipaddress.IPv4Address(raw[4:8])
        elif family == _AF_INET6 and len(raw) >= 24:
            port, address = struct.unpack("!H", raw[2:4])[0], ipaddress.IPv6Address(raw[8:24])
        else:
            return None
    except (struct.error, ValueError):
        return None
    if port == 53:
        return "a DNS lookup (network is off)"
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_unspecified:
        return None  # a service the tool runs itself, inside its sandbox
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"a network connection to {host}:{port}"


class _Iovec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("len", ctypes.c_size_t)]


_libc = None


def _read_memory(pid: int, address: int, length: int) -> bytes:
    """`length` bytes at `address` in process `pid` (process_vm_readv)."""
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        _libc.process_vm_readv.restype = ctypes.c_ssize_t
    buffer = ctypes.create_string_buffer(length)
    local = _Iovec(ctypes.cast(buffer, ctypes.c_void_p), length)
    remote = _Iovec(ctypes.c_void_p(address), length)
    got = _libc.process_vm_readv(pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
    if got <= 0:
        raise OSError(ctypes.get_errno(), "process_vm_readv")
    return buffer.raw[:got]


def serve(listener: int, report, alive=lambda: True) -> None:
    """Answer the listener's paused connect() calls until the sandbox exits:
    each destination goes to `report(text)`, then the call continues. Closes
    the listener when done, so a call can never stay paused (with no
    listener the kernel fails it instead)."""
    poller = select.poll()
    poller.register(listener, select.POLLIN)
    try:
        while True:
            # Kernels before 5.8 never signal that the sandbox has gone, and a
            # read would wait forever: check on the process every second.
            ready = poller.poll(1000)
            if not ready:
                if not alive():
                    return
                continue
            if any(event & (select.POLLHUP | select.POLLERR | select.POLLNVAL) for _, event in ready):
                return
            buffer = bytearray(_NOTIF.size)  # the kernel wants it zeroed
            try:
                fcntl.ioctl(listener, _IOCTL_RECV, buffer, True)
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno == errno.ENOENT:  # the call was interrupted before it was read
                    continue
                return  # the sandbox exited
            notif_id, pid, _, _, _, _, *args = _NOTIF.unpack(buffer)
            text = None
            try:
                raw = _read_memory(pid, args[1], min(int(args[2]), 128))
                text = describe(struct.unpack("=H", raw[:2])[0], raw)
            except (OSError, struct.error):
                pass
            try:
                fcntl.ioctl(listener, _IOCTL_SEND, _RESP.pack(notif_id, 0, 0, _FLAG_CONTINUE))
            except OSError as exc:
                if exc.errno != errno.ENOENT:  # the caller went away meanwhile
                    # Before Linux 5.5 there's no "continue": fail it as the
                    # empty network namespace would.
                    try:
                        fcntl.ioctl(listener, _IOCTL_SEND, _RESP.pack(notif_id, 0, -errno.ENETUNREACH, 0))
                    except OSError:
                        pass
            if text:
                report(text)
    finally:
        os.close(listener)


def socketpair() -> tuple[socket.socket, socket.socket]:
    """(hub end, sandbox end); the sandbox end is inheritable."""
    hub_end, sandbox_end = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    sandbox_end.set_inheritable(True)
    return hub_end, sandbox_end
