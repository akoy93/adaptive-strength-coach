from __future__ import annotations

import os
import subprocess
from pathlib import Path


class SecretError(RuntimeError):
    """Raised when a requested secret cannot be read from local secure storage."""


def read_keychain_internet_password(server: str, account: str) -> str:
    result = subprocess.run(
        ["security", "find-internet-password", "-s", server, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SecretError(f"No Keychain internet password found for {account} at {server}.")
    password = result.stdout.rstrip("\n")
    if password == "":
        raise SecretError(f"Keychain entry for {account} at {server} did not contain a password.")
    return password


def write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)


def write_private_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)
