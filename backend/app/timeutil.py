from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value) -> datetime | None:
    """Normalise a Firestore Timestamp / datetime / None to an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    as_datetime = getattr(value, "timestamp", None)
    if callable(as_datetime):  # google.protobuf.timestamp_pb2.Timestamp
        return datetime.fromtimestamp(value.timestamp(), tz=timezone.utc)
    raise TypeError(f"unsupported datetime value: {type(value)!r}")
