"""The one form of an agent's address, and keeping addresses out of text and log lines
(feature 005, FR-006, FR-028; research R11, R12)."""

import logging
import sys
import time
from collections.abc import Iterator
from types import MappingProxyType
from typing import Any

import pytest
from uvicorn.logging import AccessFormatter

from freyja_agent.config import AddressError, canonical_address, destination
from freyja_agent.delegates.addresses import (
    ADDRESS,
    PORT,
    AddressScrubber,
    install_log_scrubber,
)

# Part 1 (T004): the form an address is kept in, and where it leads.

# Stands for an address of more than 2,000 characters, to keep the test's name short.
TOO_LONG = "<an address that is too long>"


@pytest.mark.parametrize(
    ("entered", "kept"),
    [
        ("HTTP://LocalHost:9998/", "http://localhost:9998"),
        ("http://localhost", "http://localhost:80"),
        ("https://127.0.0.1", "https://127.0.0.1:443"),
        ("http://127.0.0.1:9998/a/", "http://127.0.0.1:9998/a"),
        ("http://127.0.0.1:9998/a/b", "http://127.0.0.1:9998/a/b"),
        ("http://[0:0:0:0:0:0:0:1]:9998", "http://[::1]:9998"),
        ("http://127.0.0.2:9998", "http://127.0.0.2:9998"),
    ],
)
def test_an_address_is_kept_in_one_form(entered: str, kept: str) -> None:
    assert canonical_address(entered) == kept
    # The kept form is itself in the kept form.
    assert canonical_address(kept) == kept


@pytest.mark.parametrize(
    ("entered", "rule"),
    [
        ("ftp://localhost:9998", "must start with http:// or https://"),
        ("localhost:9998", "must start with http:// or https://"),
        ("http://example.com:9998", "must be on this machine"),
        ("http://192.168.1.5:9998", "must be on this machine"),
        ("http://localhost.example.com:9998", "must be on this machine"),
        ("http://user@localhost:9998", "user name or a password"),
        ("http://user:secret@localhost:9998", "user name or a password"),
        ("http://:secret@localhost:9998", "user name or a password"),
        ("http://localhost:9998/?x=1", "must not carry a query"),
        ("http://localhost:9998/?", "must not carry a query"),
        ("http://localhost:9998/#top", "must not carry a fragment"),
        ("http://localhost:9998//a", "empty, '.', or '..' segment"),
        ("http://localhost:9998/a//b", "empty, '.', or '..' segment"),
        ("http://localhost:9998/a/./b", "empty, '.', or '..' segment"),
        ("http://localhost:9998/a/..", "empty, '.', or '..' segment"),
        ("http://localhost:9998/a%2Fb", "must not contain a percent sign"),
        ("http://localhost:9998/a\\b", "must not contain a backslash"),
        (TOO_LONG, "longer than 2,000 characters"),
        ("http://[::1", "is not a web address"),
        ("http://localhost:abc", "is not a web address"),
        ("http://localhost:99999", "is not a web address"),
        ("http://localhost:0", "port must be between 1 and 65535"),
        ("http://localhost:9998/a b", "spaces or control characters"),
        ("http://localhost:9998/a{b}", "may hold only letters, digits"),
        ("http://localhost:9998/caf\u00e9", "may hold only letters, digits"),
        ('http://localhost:9998/a"b', "may hold only letters, digits"),
        ("http://localhost:9998/a\u00a0b", "may hold only letters, digits"),
        ("http://localhost:9998/a\ud800b", "may hold only letters, digits"),
        ("http://localhost:9998/a|b", "may hold only letters, digits"),
        ("http://local\thost:9998", "spaces or control characters"),
        (" http://localhost:9998", "spaces or control characters"),
    ],
)
def test_an_address_that_breaks_a_rule_is_refused_in_the_services_own_words(
    entered: str, rule: str
) -> None:
    if entered == TOO_LONG:
        entered = "http://localhost:9998/" + "a" * 2000
    with pytest.raises(AddressError) as refused:
        canonical_address(entered)
    message = str(refused.value)
    assert rule in message
    # The sentence names the rule and never repeats what was entered.
    for part in ("9998", "example.com", "192.168", "secret", "user@", "top", "x=1", "abc"):
        assert part not in message
    # Nothing of the parser's own error rides along.
    error = refused.value
    assert error.__cause__ is None
    assert error.__context__ is None


def test_a_path_may_hold_what_is_sent_as_it_is_written() -> None:
    path = "/a-b._~!$&'()*+,;=:@/Z9"
    assert canonical_address(f"http://localhost:9998{path}") == f"http://localhost:9998{path}"


def test_the_limit_of_two_thousand_characters_is_on_the_kept_form_too() -> None:
    exactly = "http://localhost:9998/" + "a" * (2000 - len("http://localhost:9998/"))
    assert len(exactly) == 2000
    assert canonical_address(exactly) == exactly
    # Writing the port out makes this one longer than it was entered.
    without_port = "http://localhost/" + "a" * (2000 - len("http://localhost/"))
    assert len(without_port) == 2000
    with pytest.raises(AddressError, match="longer than 2,000 characters"):
        canonical_address(without_port)


def test_where_an_address_leads_is_its_port_and_path_however_this_machine_is_written() -> None:
    same = {
        destination("http://localhost:9998/a"),
        destination("http://127.0.0.1:9998/a"),
        destination("https://[::1]:9998/a"),
        destination("HTTP://LOCALHOST:9998/a/"),
    }
    assert same == {"9998/a"}
    assert destination("http://localhost:9998/a") != destination("http://localhost:9998/b")
    assert destination("http://localhost:9998") != destination("http://localhost:9997")
    assert destination("http://localhost") == "80"
    with pytest.raises(AddressError):
        destination("http://example.com:9998")


# Part 2 (T005): the scrubber and the log record factory.

URL = "http://127.0.0.1:9998"


@pytest.fixture
def scrubber() -> AddressScrubber:
    made = AddressScrubber()
    made.mark_saved(URL)
    return made


@pytest.mark.parametrize(
    "text",
    [
        "could not reach http://127.0.0.1:9998",
        "could not reach http://127.0.0.1:9998/.well-known/agent-card.json",
        "could not reach 127.0.0.1:9998",
        "could not reach localhost:9998",
        "could not reach [::1]:9998",
        "Connect call failed ('127.0.0.1', 9998)",
        "Connect call failed ('localhost', 9998)",
        "Connect call failed ('::1', 9998)",
        # As asyncio words a refused connection over IPv6.
        "Connect call failed ('::1', 9998, 0, 0)",
        # Any other way this machine is written, with the agent's port.
        "Connect call failed ('127.0.0.2', 9998)",
        "could not reach 127.0.0.2:9998",
        "could not reach http://[::ffff:127.0.0.1]:9998/x",
        "could not reach http://user:secret@127.0.0.1:9998/x",
        "could not reach HTTP://127.0.0.1:9998",
    ],
)
def test_an_address_is_removed_in_every_form(scrubber: AddressScrubber, text: str) -> None:
    cleaned = scrubber.scrub(text)
    assert ADDRESS in cleaned
    assert "9998" not in cleaned


@pytest.mark.parametrize(
    "text",
    [
        # After a scheme other than http and https, and after none.
        "reach me at ws://127.0.0.1:9998/a2a today",
        "reach me at grpc://localhost:9998 today",
        "reach me at tcp://[::1]:9998 today",
        "reach me at //127.0.0.1:9998/a2a today",
        "reach me at wss://user:secret@localhost:9998/x today",
        "see docs/127.0.0.1:9998 today",
        # However `localhost` is written: a name has no case, and may end in a full stop.
        "reach me at http://LOCALHOST:9998/a2a today",
        "reach me at HTTP://LocalHost:9998/ today",
        "reach me at http://localhost.:9998 today",
        "reach me at LocalHost:9998 today",
        "reach me at localhost.:9998 today",
        "Connect call failed ('LOCALHOST', 9998)",
    ],
)
def test_an_address_is_removed_after_any_scheme_and_however_localhost_is_written(
    scrubber: AddressScrubber, text: str
) -> None:
    """Found by the independent review of 005 Phase 9: text an agent writes about itself."""
    cleaned = scrubber.scrub(text)
    assert ADDRESS in cleaned
    assert "9998" not in cleaned


def test_the_path_of_an_address_goes_with_it_whatever_follows_in_the_sentence() -> None:
    """Found by the same review: a full stop after an address is not part of its path."""
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://127.0.0.1:9998/a2a/tok")
    scrubber.mark_saved("http://127.0.0.1:9997/a,b/c")
    for text, cleaned in [
        ("Reach me at http://127.0.0.1:9998/a2a/tok.", f"Reach me at {ADDRESS}."),
        ("Reach me at http://127.0.0.1:9998/a2a/tok, or call.", f"Reach me at {ADDRESS}, or call."),
        ("(http://127.0.0.1:9998/a2a/tok)", f"({ADDRESS})"),
        ("see http://127.0.0.1:9998/a2a/tok?x=1", f"see {ADDRESS}?x=1"),
        ("see http://127.0.0.1:9998/a2a/tok#top", f"see {ADDRESS}#top"),
        ("see localhost:9998/a2a/tok; then", f"see {ADDRESS}; then"),
        ("see ws://127.0.0.1:9998/a2a/tok.", f"see ws://{ADDRESS}."),
        # A path a saved address may hold: a comma does not end it.
        ("see http://127.0.0.1:9997/a,b/c.", f"see {ADDRESS}."),
        # Another path on the same listener loses its host and port, and keeps its path.
        ("see http://127.0.0.1:9998/a2a/token", f"see {ADDRESS}/a2a/token"),
        ("see http://127.0.0.1:9998/a2a/tok-2", f"see {ADDRESS}/a2a/tok-2"),
        ("see http://127.0.0.1:9998/a2a/tok/more", f"see {ADDRESS}/more"),
    ]:
        assert scrubber.scrub(text) == cleaned, text


def test_an_environment_address_s_query_and_fragment_go_with_it() -> None:
    """An environment agent's address may carry a credential in its query or its fragment
    (FR-017); a library that requests it writes them after the path, or after the host and
    port when there is no path, and the card's path after them (005 Phase 11, T123)."""
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://localhost:9736/a2a?token=s3cret")
    scrubber.mark_saved("http://localhost:9737?token=s3cret#frag")
    scrubber.mark_saved("http://localhost:9738/a2a/#frag")
    card = "/.well-known/agent-card.json"
    for text, cleaned in [
        (f"GET http://localhost:9736/a2a?token=s3cret{card}", f"GET {ADDRESS}{card}"),
        ("GET http://localhost:9736/a2a?token=s3cret failed", f"GET {ADDRESS} failed"),
        (f"GET http://localhost:9737?token=s3cret#frag{card}", f"GET {ADDRESS}{card}"),
        ("reached localhost:9737?token=s3cret#frag.", f"reached {ADDRESS}."),
        (f"GET http://localhost:9738/a2a/#frag{card}", f"GET {ADDRESS}{card}"),
        # A query that is not the address's own is put back, as before.
        ("GET http://localhost:9736/a2a?other=1", f"GET {ADDRESS}?other=1"),
        # A client sends the query without the fragment, and may write either encoded.
        (f"GET http://localhost:9737?token=s3cret{card}", f"GET {ADDRESS}{card}"),
        (f"GET http://localhost:9737?token=s3cr%65t{card}", f"GET {ADDRESS}{card}"),
        ("text localhost:9737#frag here", f"text {ADDRESS} here"),
        ("GET http://localhost:9736/a2a/more?token=s3cret", f"GET {ADDRESS}/more?token=s3cret"),
    ]:
        assert scrubber.scrub(text) == cleaned, text


def test_what_follows_an_address_without_a_path_is_put_back_whole() -> None:
    """A query right after the host and port is not taken for a path (review of Phase 11)."""
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://127.0.0.1:9998/a")
    for text, cleaned in [
        ("Is it http://127.0.0.1:9998?next=/a/b here", f"Is it {ADDRESS}?next=/a/b here"),
        ("see 127.0.0.1:9998?q=/a", f"see {ADDRESS}?q=/a"),
        ("see http://127.0.0.1:9998#a/a", f"see {ADDRESS}#a/a"),
    ]:
        assert scrubber.scrub(text) == cleaned, text


def test_the_whole_address_goes_before_a_part_of_it() -> None:
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://localhost:9998/agents/one")
    assert scrubber.scrub("GET http://localhost:9998/agents/one failed") == f"GET {ADDRESS} failed"
    # Another path on the same listener loses its host and port, and keeps its path.
    assert scrubber.scrub("GET http://localhost:9998/other") == f"GET {ADDRESS}/other"


def test_an_address_on_a_default_port_is_removed_as_it_is_requested() -> None:
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://localhost:80")
    scrubber.mark_saved("https://127.0.0.1:443/agent")
    card = "/.well-known/agent-card.json"
    # A client leaves a default port out of what it requests and logs.
    assert scrubber.scrub(f"GET http://localhost{card}") == f"GET {ADDRESS}{card}"
    assert scrubber.scrub(f"GET https://127.0.0.1/agent{card}") == f"GET {ADDRESS}{card}"
    assert scrubber.scrub("reached localhost:80") == f"reached {ADDRESS}"
    # The host alone is no address: another service on this machine keeps its name.
    for text in (
        "postgresql://freyja@localhost:5434/freyja",
        "listening on localhost",
        "http://localhost:8000/healthz",
    ):
        assert scrubber.scrub(text) == text


def test_registering_an_address_without_its_port_does_not_make_the_host_an_address() -> None:
    scrubber = AddressScrubber()
    scrubber.register("http://localhost")
    assert scrubber.scrub("postgresql://freyja@localhost:5434/freyja") == (
        "postgresql://freyja@localhost:5434/freyja"
    )
    assert scrubber.scrub("http://localhost/x") == f"{ADDRESS}/x"


def test_a_path_a_library_wrote_encoded_is_removed_with_its_address() -> None:
    scrubber = AddressScrubber()
    scrubber.mark_saved("http://127.0.0.1:9998/agents/a{b}")
    line = "GET http://127.0.0.1:9998/agents/a%7Bb%7D/.well-known/agent-card.json"
    assert scrubber.scrub(line) == f"GET {ADDRESS}/.well-known/agent-card.json"


def test_what_is_no_address_is_not_registered() -> None:
    scrubber = AddressScrubber()
    values: list[Any] = [None, 7, b"http://127.0.0.1:9998", "", "not an address", "http://[::1"]
    for value in values:
        scrubber.register(value)
        scrubber.mark_saved(value)
    assert scrubber.empty
    scrubber.mark_saved(URL)
    assert scrubber.scrub("127.0.0.1:9998") == ADDRESS


def test_cleaning_costs_the_same_however_many_addresses_are_known() -> None:
    scrubber = AddressScrubber()
    for number in range(20):
        scrubber.mark_saved(f"http://127.0.0.1:{7000 + number}/agents/{number}")
    for number in range(200):
        scrubber.register(f"http://127.0.0.1:{8000 + number}")
    text = ("status 200 for http://example.com:9998/x and 127.0.0.1:7003, " * 4000)[:200_000]
    began = time.perf_counter()
    cleaned = scrubber.scrub(text)
    assert time.perf_counter() - began < 2
    assert "127.0.0.1:7003" not in cleaned
    assert "http://example.com:9998/x" in cleaned


def test_when_in_doubt_an_address_is_removed(scrubber: AddressScrubber) -> None:
    # A letter or a hyphen before an IP address does not make it another address.
    assert scrubber.scrub("skill at-127.0.0.1:9998") == f"skill at-{ADDRESS}"
    assert scrubber.scrub("host=127.0.0.1:9998;") == f"host={ADDRESS};"
    assert scrubber.scrub("my-localhost:9998") == f"my-{ADDRESS}"
    # At the end of a sentence, and before a path.
    assert scrubber.scrub("It listens on localhost:9998.") == f"It listens on {ADDRESS}."
    assert scrubber.scrub("see 127.0.0.1:9998/a2a, then") == f"see {ADDRESS}/a2a, then"
    assert scrubber.scrub_ports("port 9998.") == f"port {PORT}."
    assert scrubber.scrub_ports("version 9998.1 and 1.9998") == "version 9998.1 and 1.9998"
    # A longer number is another address, and a longer name another host.
    assert scrubber.scrub("1127.0.0.1:9998") == "1127.0.0.1:9998"
    assert scrubber.scrub("10.127.0.0.1:9998") == "10.127.0.0.1:9998"


def test_text_without_an_address_comes_back_unchanged(scrubber: AddressScrubber) -> None:
    for text in (
        "Agent stub: gated, 2 skills",
        "port 99980 and 19998 are other numbers",
        "mylocalhost:9998 is another host",
        "127.0.0.1:99981",
        "",
    ):
        assert scrubber.scrub(text) == text
    assert AddressScrubber().scrub("http://127.0.0.1:9998") == "http://127.0.0.1:9998"


def test_an_address_that_was_never_saved_is_forgotten_after_two_hundred_later_ones() -> None:
    scrubber = AddressScrubber()
    scrubber.register("http://127.0.0.1:7001")
    scrubber.mark_saved("http://127.0.0.1:7002")
    for number in range(200):
        scrubber.register(f"http://127.0.0.1:{8000 + number}")
    # Still known: 200 unsaved addresses are remembered, and this is the 201st from the end.
    assert scrubber.scrub("127.0.0.1:8000") == ADDRESS
    assert scrubber.scrub("127.0.0.1:7001") == "127.0.0.1:7001"
    scrubber.register("http://127.0.0.1:8200")
    assert scrubber.scrub("127.0.0.1:8000") == "127.0.0.1:8000"
    assert scrubber.scrub("127.0.0.1:8001") == ADDRESS
    # A saved address is kept whatever is registered after it.
    assert scrubber.scrub("127.0.0.1:7002") == ADDRESS


def test_registering_again_changes_nothing_and_saving_keeps_a_registered_address() -> None:
    scrubber = AddressScrubber(unsaved_limit=2)
    scrubber.register("http://127.0.0.1:7001")
    scrubber.register("http://127.0.0.1:7001")
    scrubber.mark_saved("http://127.0.0.1:7001")
    for number in range(5):
        scrubber.register(f"http://127.0.0.1:{8000 + number}")
    assert scrubber.scrub("127.0.0.1:7001") == ADDRESS


@pytest.fixture
def factory(scrubber: AddressScrubber) -> Iterator[None]:
    """The log record factory, installed for one test and taken out again.

    The app installs the process's own when it is created, and a factory installs once, so
    the test starts from a plain one to get its own scrubber.
    """
    before = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    install_log_scrubber(scrubber)
    yield
    logging.setLogRecordFactory(before)


def record_of(name: str, message: object, *arguments: object) -> str:
    """What a handler would write for one record of the logger ``name``."""
    logger = logging.getLogger(name)
    record = logger.makeRecord(
        name,
        logging.INFO,
        __file__,
        1,
        message,
        arguments,
        exc_info=None,
    )
    return logging.Formatter("%(name)s %(message)s").format(record)


@pytest.mark.usefixtures("factory")
def test_the_http_librarys_request_line_holds_no_address() -> None:
    import httpx

    url = httpx.URL(f"{URL}/.well-known/agent-card.json")
    line = record_of("httpx", 'HTTP Request: %s %s "%s %d %s"', "GET", url, "HTTP/1.1", 200, "OK")
    assert (
        line == f'httpx HTTP Request: GET {ADDRESS}/.well-known/agent-card.json "HTTP/1.1 200 OK"'
    )


def test_the_http_librarys_request_line_for_an_address_with_a_query_holds_none_of_it() -> None:
    import httpx

    scrubber = AddressScrubber()
    scrubber.mark_saved("http://localhost:9736/a2a?token=s3cret")
    before = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        install_log_scrubber(scrubber)
        # How the agent library joins the card's path to an address that has a query.
        url = httpx.URL("http://localhost:9736/a2a?token=s3cret/.well-known/agent-card.json")
        line = record_of(
            "httpx", 'HTTP Request: %s %s "%s %d %s"', "GET", url, "HTTP/1.1", 404, "Not Found"
        )
    finally:
        logging.setLogRecordFactory(before)
    assert line == (
        f'httpx HTTP Request: GET {ADDRESS}/.well-known/agent-card.json "HTTP/1.1 404 Not Found"'
    )


@pytest.mark.usefixtures("factory")
def test_a_port_that_stands_apart_is_removed_in_the_libraries_records_only() -> None:
    message = "connect_tcp.started host='127.0.0.1' port=9998 local_address=None timeout=5.0"
    line = record_of("httpcore.connection", message)
    assert "9998" not in line
    assert f"port={PORT}" in line
    assert "9998" not in record_of("a2a.client.card_resolver", "fetching card from port %s", 9998)
    # Any other logger's number is its own business.
    assert record_of("freyja_agent.api", "waited 9998 ms") == "freyja_agent.api waited 9998 ms"
    assert record_of("uvicorn.error", "handled %d requests", 9998).endswith("handled 9998 requests")


@pytest.mark.usefixtures("factory")
def test_a_number_a_format_asked_for_does_not_break_the_line() -> None:
    line = record_of("httpx", "connected to port %d in %d ms", 9998, 12)
    assert line == f"httpx connected to port {PORT} in 12 ms"


@pytest.mark.usefixtures("factory")
def test_an_argument_that_is_not_text_is_replaced_only_when_cleaning_changes_it() -> None:
    logger = logging.getLogger("freyja_agent.test")
    kept = {"status": 200}
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, "%s %s %s", (kept, 404, ("127.0.0.1", 9998)), None
    )
    assert record.args is not None
    first, second, third = record.args
    assert first is kept
    assert second == 404
    assert third == ADDRESS
    assert record.getMessage() == f"{{'status': 200}} 404 {ADDRESS}"


@pytest.mark.usefixtures("factory")
def test_arguments_given_as_a_mapping_are_cleaned() -> None:
    logger = logging.getLogger("freyja_agent.test")
    for arguments in ({"where": "127.0.0.1:9998"}, MappingProxyType({"where": "127.0.0.1:9998"})):
        record = logger.makeRecord(
            logger.name, logging.INFO, __file__, 1, "reached %(where)s", (arguments,), None
        )
        assert record.getMessage() == f"reached {ADDRESS}"


def test_a_record_that_cannot_be_cleaned_is_handed_on_as_it_was_made(
    scrubber: AddressScrubber, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = logging.getLogRecordFactory()

    def broken(_text: str) -> str:
        raise RuntimeError("the scrubber failed")

    install_log_scrubber(scrubber)
    monkeypatch.setattr(scrubber, "scrub", broken)
    try:
        # Logging goes on: the line is written, whatever became of the cleaning.
        assert (
            record_of("freyja_agent.test", "waited %d ms", 12) == "freyja_agent.test waited 12 ms"
        )
    finally:
        logging.setLogRecordFactory(before)


@pytest.mark.usefixtures("factory")
def test_uvicorns_access_line_still_formats() -> None:
    logger = logging.getLogger("uvicorn.access")
    arguments = ("127.0.0.1:51234", "POST", "/threads/t1/runs", "1.1", 200)
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', arguments, None
    )
    # The five arguments are where the formatter looks for them, and of the kinds it expects.
    assert record.args == arguments
    line = AccessFormatter("%(client_addr)s %(request_line)s %(status_code)s").format(record)
    assert "127.0.0.1:51234" in line
    assert "POST /threads/t1/runs HTTP/1.1" in line
    assert "200" in line


@pytest.mark.usefixtures("factory")
def test_a_traceback_is_cleaned_and_cannot_be_formatted_again_with_the_address() -> None:
    logger = logging.getLogger("freyja_agent.test")
    try:
        try:
            raise ConnectionRefusedError("[Errno 111] Connect call failed ('127.0.0.1', 9998)")
        except ConnectionRefusedError as cause:
            raise RuntimeError("the agent could not be reached") from cause
    except RuntimeError:
        record = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 1, "run failed", (), sys.exc_info()
        )
    assert record.exc_info is None
    assert record.exc_text is not None
    assert "ConnectionRefusedError" in record.exc_text
    assert "the agent could not be reached" in record.exc_text
    assert "9998" not in record.exc_text
    # A handler with a formatter of its own writes the cleaned text, not a new one.
    for formatter in (logging.Formatter(), logging.Formatter("%(levelname)s %(message)s")):
        assert "9998" not in formatter.format(record)


@pytest.mark.usefixtures("factory")
def test_a_traceback_without_an_address_is_left_as_it_was() -> None:
    logger = logging.getLogger("freyja_agent.test")
    try:
        raise ValueError("nothing to hide")
    except ValueError:
        record = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
    assert record.exc_info is not None
    assert record.exc_text is None


def test_installing_twice_installs_once_and_the_earlier_factory_still_makes_the_record(
    scrubber: AddressScrubber,
) -> None:
    before = logging.getLogRecordFactory()
    made: list[str] = []

    def earlier(*args: object, **kwargs: object) -> logging.LogRecord:
        record = before(*args, **kwargs)
        made.append(record.name)
        return record

    logging.setLogRecordFactory(earlier)
    try:
        install_log_scrubber(scrubber)
        installed = logging.getLogRecordFactory()
        install_log_scrubber(scrubber)
        assert logging.getLogRecordFactory() is installed
        line = record_of("freyja_agent.test", "reached %s", "127.0.0.1:9998")
        assert line == f"freyja_agent.test reached {ADDRESS}"
        assert made == ["freyja_agent.test"]
    finally:
        logging.setLogRecordFactory(before)


def test_with_no_address_known_a_record_is_made_as_before() -> None:
    before = logging.getLogRecordFactory()
    install_log_scrubber(AddressScrubber())
    try:
        line = record_of("httpcore.connection", "connect_tcp.started host='127.0.0.1' port=9998")
        assert line.endswith("port=9998")
    finally:
        logging.setLogRecordFactory(before)
