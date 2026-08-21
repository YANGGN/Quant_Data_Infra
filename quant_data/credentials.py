"""Named-only, dependency-free project ``.env`` credential access.

The helper deliberately reads a single requested assignment rather than loading a
dotenv file into process state.  A nonblank caller-supplied environment value
has precedence and does not touch the filesystem.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import stat

from .errors import ValidationError


_CREDENTIAL_ERROR = "Credential is missing or invalid"
_DOTENV_NAME = ".env"
_MAX_DOTENV_BYTES = 64 * 1024
_MAX_CREDENTIAL_BYTES = 4096
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HORIZONTAL_WHITESPACE = " \t"


def _invalid_credential() -> ValidationError:
    """Return the one safe credential error without source details."""

    return ValidationError(_CREDENTIAL_ERROR)


def _validate_name(name: object) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise _invalid_credential()
    return name


def _validate_value(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid_credential()
    try:
        value_bytes = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise _invalid_credential() from exc
    if len(value_bytes) > _MAX_CREDENTIAL_BYTES:
        raise _invalid_credential()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _invalid_credential()
    return value


def _process_environment_value(environment: Mapping[str, str], name: str) -> str | None:
    if not isinstance(environment, Mapping):
        raise _invalid_credential()
    try:
        value = environment.get(name)
    except Exception as exc:
        raise _invalid_credential() from exc
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid_credential()
    if not value.strip():
        return None
    return _validate_value(value)


def _resolved_project_root(project_root: Path) -> Path:
    if not isinstance(project_root, Path):
        raise _invalid_credential()
    try:
        resolved = project_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise _invalid_credential() from exc
    if not resolved.is_dir():
        raise _invalid_credential()
    return resolved


def _validate_open_file(file_descriptor: int) -> None:
    try:
        metadata = os.fstat(file_descriptor)
    except OSError as exc:
        raise _invalid_credential() from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or metadata.st_size > _MAX_DOTENV_BYTES
    ):
        raise _invalid_credential()


def _read_dotenv_bytes(project_root: Path) -> bytes:
    """Open exactly the physical project ``.env`` with no symlink following."""

    dotenv_path = _resolved_project_root(project_root) / _DOTENV_NAME
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    except AttributeError as exc:
        raise _invalid_credential() from exc
    try:
        file_descriptor = os.open(os.fspath(dotenv_path), flags)
    except OSError as exc:
        raise _invalid_credential() from exc
    try:
        _validate_open_file(file_descriptor)
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(file_descriptor, min(8192, _MAX_DOTENV_BYTES + 1 - total))
            except OSError as exc:
                raise _invalid_credential() from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_DOTENV_BYTES:
                raise _invalid_credential()
        _validate_open_file(file_descriptor)
        return b"".join(chunks)
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass


def _decode_dotenv(source: bytes) -> str:
    try:
        decoded = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _invalid_credential() from exc
    # Newlines and tabs are structural dotenv whitespace.  Every other ASCII
    # control character (including NUL and DEL) is unsafe credential source
    # material and is rejected before parsing.
    if any(
        (ord(character) < 32 and character not in "\t\n\r")
        or ord(character) == 127
        for character in decoded
    ):
        raise _invalid_credential()
    return decoded


def _strip_trailing_comment(value: str) -> str:
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1] in _HORIZONTAL_WHITESPACE):
            return value[:index].rstrip(_HORIZONTAL_WHITESPACE)
    return value.rstrip(_HORIZONTAL_WHITESPACE)


def _parse_assignment_value(remainder: str) -> str:
    value_text = remainder.lstrip(_HORIZONTAL_WHITESPACE)
    if not value_text.startswith("="):
        raise _invalid_credential()
    value_text = value_text[1:].lstrip(_HORIZONTAL_WHITESPACE)
    if value_text.startswith(("'", '"')):
        quote = value_text[0]
        end = value_text.find(quote, 1)
        if end < 0:
            raise _invalid_credential()
        value = value_text[1:end]
        suffix = value_text[end + 1 :].strip(_HORIZONTAL_WHITESPACE)
        if suffix and not suffix.startswith("#"):
            raise _invalid_credential()
    else:
        value = _strip_trailing_comment(value_text)
    return _validate_value(value)


def _requested_assignment(line: str, name: str) -> str | None:
    stripped = line.strip(_HORIZONTAL_WHITESPACE)
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export") and (
        len(stripped) == len("export")
        or stripped[len("export")] in _HORIZONTAL_WHITESPACE
    ):
        stripped = stripped[len("export") :].lstrip(_HORIZONTAL_WHITESPACE)
        if not stripped:
            return None
    if not stripped.startswith(name):
        return None
    remainder = stripped[len(name) :]
    if remainder and (remainder[0].isalnum() or remainder[0] == "_"):
        return None
    if not remainder:
        raise _invalid_credential()
    return _parse_assignment_value(remainder)


def _dotenv_value(project_root: Path, name: str) -> str:
    source = _decode_dotenv(_read_dotenv_bytes(project_root))
    found: str | None = None
    for line in source.splitlines():
        value = _requested_assignment(line, name)
        if value is None:
            continue
        if found is not None:
            raise _invalid_credential()
        found = value
    if found is None:
        raise _invalid_credential()
    return found


def read_project_credential(
    *,
    project_root: Path,
    name: str,
    environment: Mapping[str, str],
) -> str:
    """Return one validated credential from environment or secure project ``.env``.

    A nonblank process-environment value takes precedence.  The fallback never
    exports a dotenv assignment, performs interpolation, or mutates caller or
    process state.
    """

    requested_name = _validate_name(name)
    process_value = _process_environment_value(environment, requested_name)
    if process_value is not None:
        return process_value
    return _dotenv_value(project_root, requested_name)


__all__ = ("read_project_credential",)
