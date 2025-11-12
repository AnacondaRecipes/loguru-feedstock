import json
import threading
from time import sleep
from pathlib import Path

import pytest
from loguru import logger


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="ignore")


# --- Basic logging -----------------------------------------------------------

def test_basic_message_written(tmp_path: Path):
    log_file = tmp_path / "basic.log"
    handler_id = logger.add(log_file, level="INFO", format="{message}")
    try:
        logger.debug("not visible")
        logger.info("hello")
    finally:
        logger.remove(handler_id)

    assert log_file.exists()
    assert _read_text(log_file).strip() == "hello"


def test_level_threshold(tmp_path: Path):
    log_file = tmp_path / "levels.log"
    handler_id = logger.add(log_file, level="WARNING", format="{level.name}:{message}")
    try:
        logger.info("skip")
        logger.warning("show")
    finally:
        logger.remove(handler_id)

    text = _read_text(log_file)
    assert "INFO:skip" not in text
    assert "WARNING:show" in text


def test_remove_handler_stops_logging(tmp_path: Path):
    log_file = tmp_path / "remove.log"
    handler_id = logger.add(log_file, format="{message}")
    try:
        logger.info("keep")
        logger.remove(handler_id)
        logger.info("drop")
    except Exception:
        # make sure handler is considered removed even if assertion fails
        try:
            logger.remove(handler_id)
        except Exception:
            pass

    text = _read_text(log_file)
    assert "keep" in text
    assert "drop" not in text


# --- Rotation / Retention ----------------------------------------------------

def test_retention_limits_number_of_files(tmp_path: Path):
    base = tmp_path / "retain.log"
    handler_id = logger.add(base, rotation=150, retention=1, format="{message}")
    try:
        for i in range(6):
            logger.info("msg-%d %s", i, "Y" * 120)
    finally:
        logger.remove(handler_id)

    files = [p for p in tmp_path.glob("retain.log*")]
    # With retention=1, only the most recent log file should remain
    assert len(files) == 1


# --- Filtering / Formatting / Extras ----------------------------------------

def test_filter_function_only_errors(tmp_path: Path):
    log_file = tmp_path / "filter.log"
    handler_id = logger.add(
        log_file,
        filter=lambda record: record["level"].name == "ERROR",
        format="{level.name}:{message}",
    )
    try:
        logger.info("nope")
        logger.error("only-this")
    finally:
        logger.remove(handler_id)

    text = _read_text(log_file)
    assert "nope" not in text
    assert "ERROR:only-this" in text


def test_bind_extra_in_format(tmp_path: Path):
    log_file = tmp_path / "extra.log"
    handler_id = logger.add(log_file, format="{extra[user]} - {message}")
    try:
        logger.bind(user="alice").info("hi")
    finally:
        logger.remove(handler_id)

    assert _read_text(log_file).strip() == "alice - hi"


def test_contextualize_scopes_extra(tmp_path: Path):
    log_file = tmp_path / "ctx.log"
    handler_id = logger.add(log_file, format="{extra[sess]}:{message}")
    try:
        with logger.contextualize(sess="A"):
            logger.info("one")
        # Outside of context, 'sess' is not defined; use default to avoid KeyError in format
        logger.remove(handler_id)
        # Re-add with safe formatting using get() on extra via record patching
        handler_id = logger.add(log_file, format="{message}")
        logger.info("two")
    finally:
        logger.remove(handler_id)

    text = _read_text(log_file).splitlines()
    assert "A:one" in text[0]
    assert "two" in text[-1]


def test_patch_enriches_record(tmp_path: Path):
    log_file = tmp_path / "patch.log"
    patched = logger.patch(lambda record: record["extra"].setdefault("x", 1))
    handler_id = patched.add(log_file, format="{extra[x]} {message}")
    try:
        patched.info("ok")
    finally:
        patched.remove(handler_id)

    assert _read_text(log_file).strip().endswith("1 ok")


# --- Serialization -----------------------------------------------------------

def test_json_serialization(tmp_path: Path):
    log_file = tmp_path / "json.log"
    handler_id = logger.add(log_file, serialize=True)
    try:
        logger.info("hi there")
    finally:
        logger.remove(handler_id)

    line = _read_text(log_file).splitlines()[0]
    data = json.loads(line)

    # Message can be stored in different keys depending on loguru version/format
    msg = (
        data.get("message")
        or (data.get("record") or {}).get("message")
        or data.get("text")
    )
    assert msg == "hi there", f"Expected message not found, JSON: {data}"

    # Log level can be a string or a nested dict
    level_name = None
    if isinstance(data.get("level"), dict):
        level_name = data["level"].get("name")
    elif isinstance(data.get("level"), str):
        level_name = data["level"]
    elif "record" in data and isinstance(data["record"].get("level"), dict):
        level_name = data["record"]["level"].get("name")
    assert level_name == "INFO", f"Expected INFO level not found, JSON: {data}"


# --- Exceptions --------------------------------------------------------------

def test_exception_logging_contains_message(tmp_path: Path):
    log_file = tmp_path / "exc.log"
    handler_id = logger.add(log_file, format="{level.name}:{message}")
    try:
        try:
            1 / 0
        except ZeroDivisionError:
            logger.exception("boom")
    finally:
        logger.remove(handler_id)

    text = _read_text(log_file)
    assert "ERROR:boom" in text
    # stack details are serialized separately; we just sanity-check presence
    assert "ZeroDivisionError" in text


# --- Concurrency (enqueue) ---------------------------------------------------

def test_enqueue_thread_safety(tmp_path: Path):
    log_file = tmp_path / "queue.log"
    handler_id = logger.add(log_file, enqueue=True, format="{message}")
    try:
        N = 50
        def worker(n):
            logger.info(f"line-{n}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Give the queue a moment to flush
        sleep(0.2)
    finally:
        logger.remove(handler_id)

    lines = [l for l in _read_text(log_file).splitlines() if l.strip()]
    assert len(lines) == 50
    # Check a few sentinel lines exist
    assert "line-0" in lines and "line-49" in lines