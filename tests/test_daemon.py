import socket
import tempfile
from pathlib import Path

import pytest

from codex_coordinator.daemon import probe_local_socket, validate_local_socket


def test_socket_target_must_be_private_unix_socket(tmp_path: Path):
    target = tmp_path / "control.sock"
    target.write_text("not a socket")
    with pytest.raises(ValueError, match="not a Unix socket"):
        validate_local_socket(target)
    link = tmp_path / "linked.sock"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="not a Unix socket"):
        validate_local_socket(link)


def test_owner_controlled_symlink_to_private_unix_socket_is_accepted():
    with tempfile.TemporaryDirectory(prefix="cc-socket-", dir="/tmp") as directory:
        root = Path(directory)
        target_parent = root / "daemon"
        target_parent.mkdir(mode=0o700)
        target = target_parent / "control.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(target))
            target.chmod(0o600)
            link = root / "linked.sock"
            link.symlink_to(target)
            validate_local_socket(link)


def test_socket_symlink_target_parent_must_be_owner_controlled():
    with tempfile.TemporaryDirectory(prefix="cc-socket-", dir="/tmp") as directory:
        root = Path(directory)
        target_parent = root / "shared"
        target_parent.mkdir()
        target = target_parent / "control.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(target))
            target.chmod(0o600)
            link = root / "linked.sock"
            link.symlink_to(target)
            target_parent.chmod(0o777)
            try:
                with pytest.raises(ValueError, match="target parent must be owner-controlled"):
                    validate_local_socket(link)
            finally:
                target_parent.chmod(0o700)


def test_socket_parent_must_be_owner_controlled(tmp_path: Path):
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="parent must be owner-controlled"):
            validate_local_socket(parent / "control.sock")
    finally:
        parent.chmod(0o700)


def test_probe_reports_stale_or_unreachable_socket(monkeypatch, tmp_path: Path):
    path = tmp_path / "stale.sock"
    monkeypatch.setattr("codex_coordinator.daemon.validate_local_socket", lambda _path: None)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, value):
            assert value == 2

        def connect(self, value):
            assert value == str(path)
            raise ConnectionRefusedError("stale socket")

    monkeypatch.setattr("codex_coordinator.daemon.socket.socket", lambda *_args: Socket())
    with pytest.raises(ConnectionError, match="not reachable"):
        probe_local_socket(path)
