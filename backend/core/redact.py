import logging
import re
import threading

_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}")
_known: set[str] = set()
_lock = threading.Lock()

MASK = "***"


def register_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        with _lock:
            _known.add(value)


# Stored chat (memory.db) also gets these: things people type or say that must not be kept or recalled.
_SECRET_PHRASE = re.compile(
    r"(?i)((?:密码|口令|验证码|支付码|pin码?|password|passwd|pwd|passcode|otp)\s*(?:是|为|:|：|=)?\s*)([^\s，。,;；]{3,})"
)
# Long random-looking tokens (API keys of any vendor): letters and digits mixed, 24+ chars.
_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_\-\.]*\d)(?=[A-Za-z0-9_\-\.]*[A-Za-z])[A-Za-z0-9_\-\.]{24,}")


def redact_sensitive(text: str) -> str:
    """redact() plus passwords/codes after a keyword and long key-like tokens. For stored chat text."""
    text = redact(text)
    text = _SECRET_PHRASE.sub(lambda m: m.group(1) + MASK, text)
    return _TOKEN.sub(MASK, text)


def redact(text: str) -> str:
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, MASK)
    text = _SK.sub(MASK, text)
    return _BEARER.sub(lambda m: m.group(1) + MASK, text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - mismatched format args in someone's log call
            msg = f"{record.msg} {record.args!r}"
            record.msg, record.args = msg, None
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        if record.exc_info:
            # Traceback text is rendered after filters run; pre-render it redacted (e.g. SDK errors echoing headers).
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
