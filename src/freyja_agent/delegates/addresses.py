"""Keeping agent addresses out of what the service says and logs (feature 005, research R12).

An agent's address is returned by the Agents routes and by nothing else (FR-028). This module
holds the one scrubber of the process: it is told every address the process is given, the
address of an add that is then refused included, and removes each from text in every form it
can appear in.

It is applied in three places: to text the service words or passes on about an agent, to an
agent's card details before they are kept or returned, and to every log record of the process
through a log record factory, because the HTTP and agent libraries log the addresses they
request and no handler or logger filter sees every record.

The scrubber does not search text for each address it knows. It finds everything in the text
that has the shape of an address, and looks each one up. Cleaning a text therefore costs the
same whether one address is known or two hundred, which matters because it runs on every log
record and on text an agent controls.
"""

import logging
import re
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from freyja_agent.config import is_local_host

# Stands in for an agent's address, and for its port where a library logs the port apart.
ADDRESS = "<address>"
PORT = "<port>"

# How many addresses that were never saved are remembered: the address of a refused add has
# to be known while its request is handled and logged, and need not be kept for ever.
UNSAVED_LIMIT = 200

# The libraries that make the requests to an agent. They log the host and the port apart, so
# in their records the bare port number is removed as well.
LIBRARY_LOGGERS = ("httpx", "httpcore", "a2a")

_DEFAULT_PORTS = {"http": 80, "https": 443}

# The three shapes an address takes in text. A host is a name, an IPv4 address, or an IPv6
# address in brackets; a path runs to the first character that ends an address in prose.
_HOST = r"\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+"
_PATH = r"(?:/[^\s\"'<>)\]},;]*)?"
# With its scheme: ``http://127.0.0.1:9998/a``, and ``http://localhost/a`` on a default port.
_URL = re.compile(
    rf"(?P<scheme>https?)://(?:[^\s/@'\"<>]*@)?(?P<host>{_HOST})(?::(?P<port>\d{{1,5}}))?"
    rf"(?P<path>{_PATH})",
    re.IGNORECASE,
)
# Without one: ``127.0.0.1:9998``, ``localhost:9998/a``. Not inside a longer name or number.
_HOST_AND_PORT = re.compile(
    rf"(?<![A-Za-z0-9.\-/])(?P<host>{_HOST}):(?P<port>\d{{1,5}})(?!\d|\.\d)(?P<path>{_PATH})"
)
# As the operating system names a connection: ``('127.0.0.1', 9998)``, ``('::1', 9998, 0, 0)``.
_PAIR = re.compile(r"\(\s*'(?P<host>[^']{1,255})'\s*,\s*(?P<port>\d{1,5})(?:\s*,\s*\d+)*\s*\)")
# A number of its own: not part of a longer number, a version, or an IP address. A full
# stop after it ends a sentence; a full stop and a digit continue a number.
_NUMBER = re.compile(r"(?<![\d.])\d{1,5}(?!\d|\.\d)")
# A host at the end of a longer token: ``at-127.0.0.1``, ``my-localhost``. When in doubt an
# address is removed: a letter or a hyphen before an IP address does not make it another one.
_TRAILING_IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}$")
_TRAILING_LOCALHOST = re.compile(r"(?<![A-Za-z0-9.])localhost$", re.IGNORECASE)

# Where an agent is: its port on this machine (the host is then None), or a host and a port.
Place = tuple[str | None, int]
Segments = tuple[str, ...]


def _place(url: str) -> tuple[Place, Segments] | None:
    """Where ``url`` leads and the segments of its path, or None when it is no address."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    except ValueError:
        return None
    if not host or port is None:
        return None
    path = parts.path.removesuffix("/")
    segments = tuple(unquote(segment) for segment in path.split("/")[1:])
    return (None if is_local_host(host) else host.rstrip(".").lower(), port), segments


class AddressScrubber:
    """Every agent address the process was given, and their removal from text."""

    def __init__(self, unsaved_limit: int = UNSAVED_LIMIT) -> None:
        self._unsaved_limit = unsaved_limit
        self._saved: set[str] = set()
        # In the order they were registered: the oldest is the first to be forgotten.
        self._unsaved: OrderedDict[str, None] = OrderedDict()
        # Read by every log record, on any thread: replaced whole, never changed in place.
        self._places: dict[Place, tuple[Segments, ...]] = {}
        self._ports: frozenset[int] = frozenset()

    def register(self, url: str) -> None:
        """Remember ``url`` before it is first requested, so no line about it holds it."""
        if not isinstance(url, str) or not url or url in self._saved or url in self._unsaved:
            return
        self._unsaved[url] = None
        while len(self._unsaved) > self._unsaved_limit:
            self._unsaved.popitem(last=False)
        self._rebuild()

    def mark_saved(self, url: str) -> None:
        """Keep ``url`` until the process ends: it is, or was, a configured agent's."""
        if not isinstance(url, str) or not url or url in self._saved:
            return
        self._unsaved.pop(url, None)
        self._saved.add(url)
        self._rebuild()

    def _rebuild(self) -> None:
        places: dict[Place, set[Segments]] = {}
        for url in (*self._saved, *self._unsaved):
            found = _place(url)
            if found is not None:
                places.setdefault(found[0], set()).add(found[1])
        # The longest path first, so the whole address goes before a part of it.
        self._places = {
            place: tuple(sorted(paths, key=len, reverse=True)) for place, paths in places.items()
        }
        self._ports = frozenset(port for _host, port in places)

    @property
    def empty(self) -> bool:
        """True while no address is known: nothing can be in any text."""
        return not self._places

    def _known(self, host: str, port: int) -> Place | None:
        """The place ``host`` and ``port`` name, when it is a known agent's."""
        bare = host.strip("[]")
        place: Place = (None if is_local_host(bare) else bare.rstrip(".").lower(), port)
        return place if place in self._places else None

    def _in_token(self, token: str, port: int) -> tuple[int, Place] | None:
        """Where in ``token`` a known host starts: the whole of it, or its end."""
        place = self._known(token, port)
        if place is not None:
            return 0, place
        for trailing in (_TRAILING_IPV4, _TRAILING_LOCALHOST):
            found = trailing.search(token)
            if found is not None and found.start() > 0:
                place = self._known(found.group(0), port)
                if place is not None:
                    return found.start(), place
        return None

    def _path_taken(self, place: Place, path: str) -> int:
        """How much of ``path`` is the path of an address at ``place``.

        Segment by segment and decoded, so a path a library wrote encoded is found as well.
        """
        raw = path.split("/")[1:] if path else []
        decoded = [unquote(segment) for segment in raw]
        for known in self._places[place]:
            if known and tuple(decoded[: len(known)]) == known:
                return sum(len(segment) + 1 for segment in raw[: len(known)])
        return 0

    def _url(self, match: re.Match[str]) -> str:
        scheme = match.group("scheme").lower()
        port = int(match.group("port") or _DEFAULT_PORTS[scheme])
        place = self._known(match.group("host"), port)
        if place is None:
            return match.group(0)
        path = match.group("path")
        return ADDRESS + path[self._path_taken(place, path) :]

    def _host_and_port(self, match: re.Match[str]) -> str:
        token, path = match.group("host"), match.group("path")
        found = self._in_token(token, int(match.group("port")))
        if found is None:
            return match.group(0)
        start, place = found
        return token[:start] + ADDRESS + path[self._path_taken(place, path) :]

    def _pair(self, match: re.Match[str]) -> str:
        known = self._known(match.group("host"), int(match.group("port")))
        return ADDRESS if known is not None else match.group(0)

    def scrub(self, text: str) -> str:
        """``text`` with every known address removed, in every form of FR-028."""
        if not self._places or (":" not in text and "('" not in text):
            return text
        text = _URL.sub(self._url, text)
        text = _PAIR.sub(self._pair, text)
        return _HOST_AND_PORT.sub(self._host_and_port, text)

    def _number(self, match: re.Match[str]) -> str:
        return PORT if int(match.group(0)) in self._ports else match.group(0)

    def scrub_ports(self, text: str) -> str:
        """``text`` with every known port removed where it stands as a number of its own."""
        if not self._ports:
            return text
        return _NUMBER.sub(self._number, text)


# The scrubber of the process. Links register their addresses with it (research R12).
SCRUBBER = AddressScrubber()

_INSTALLED = "_freyja_address_scrubber"
_formatter = logging.Formatter()


def _is_library(name: str) -> bool:
    return any(name == library or name.startswith(library + ".") for library in LIBRARY_LOGGERS)


def _text_of(value: Any) -> str | None:
    try:
        return str(value)
    except Exception:  # an argument whose own ``__str__`` fails is left to the formatter
        return None


def _clean_record(record: logging.LogRecord, scrubber: AddressScrubber) -> None:
    """Remove every known address from ``record``, in place.

    The message and each argument are cleaned where they stand, so the record keeps the
    number and kind of arguments its formatter expects: uvicorn's access formatter unpacks
    them. An argument that is not text is replaced by its cleaned text only when cleaning
    changes it. A traceback is formatted and cleaned into the record's own traceback text,
    and the reference to the exception is dropped when cleaning changed it, so a handler that
    formats it again cannot bring the address back.

    Not cleaned: the keys of a mapping of arguments, and attributes a caller added to the
    record with ``extra``.
    """
    library = _is_library(record.name)
    changed = False

    def clean(text: str) -> str:
        text = scrubber.scrub(text)
        return scrubber.scrub_ports(text) if library else text

    def clean_argument(value: Any) -> Any:
        nonlocal changed
        text = value if isinstance(value, str) else _text_of(value)
        if text is None:
            return value
        cleaned = clean(text)
        if cleaned == text:
            return value
        changed = True
        return cleaned

    message, arguments = record.msg, record.args
    record.msg = clean_argument(message)
    if isinstance(arguments, tuple):
        record.args = tuple(clean_argument(argument) for argument in arguments)
    elif isinstance(arguments, Mapping):
        record.args = {key: clean_argument(value) for key, value in arguments.items()}
    if changed:
        try:
            record.getMessage()
        except Exception:
            # A number a format asked for became text. Only a library's record has its bare
            # port removed, and none of their formatters reads the arguments: the line is
            # formatted as it was and cleaned whole.
            record.msg, record.args = message, arguments
            record.msg, record.args = clean(record.getMessage()), None
    if record.exc_info and record.exc_info[0] is not None:
        text = _formatter.formatException(record.exc_info)
        cleaned = clean(text)
        if cleaned != text:
            record.exc_text = cleaned
            record.exc_info = None
    if record.stack_info:
        record.stack_info = clean(record.stack_info)


def install_log_scrubber(scrubber: AddressScrubber = SCRUBBER) -> None:
    """Have every log record of the process cleaned as it is made. Installs once.

    A filter on a handler does not see a handler added later, and a filter on a logger does
    not see its children's records; the record factory sees every record. The factory that
    was there before still makes the record.

    Cleaning never raises into the code that logged: a record that cannot be cleaned is
    handed on as it was made.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _INSTALLED, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        if scrubber.empty:
            return record
        message, arguments = record.msg, record.args
        try:
            _clean_record(record, scrubber)
        except Exception:
            # Logging must not fail because of this hook, whatever the record holds.
            record.msg, record.args = message, arguments
        return record

    setattr(factory, _INSTALLED, True)
    logging.setLogRecordFactory(factory)
