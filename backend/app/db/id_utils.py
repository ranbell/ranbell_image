import uuid


class NotAPictureId(ValueError):
    """A picture id that cannot be one — not hex, or too short.

    It reaches here from a URL as often as from code (a typo, a truncated link),
    so main.py answers it with a 404 rather than letting it surface as a 500.
    A `ValueError`, so the handlers that already catch that keep working.
    """


def sha256_to_point_id(sha256: str) -> str:
    """Convert SHA256 hex string to a deterministic UUID string for use as Qdrant point ID."""
    try:
        return str(uuid.UUID(sha256[:32]))
    except (ValueError, TypeError) as e:
        raise NotAPictureId(sha256) from e
