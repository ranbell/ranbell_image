import uuid


def sha256_to_point_id(sha256: str) -> str:
    """Convert SHA256 hex string to a deterministic UUID string for use as Qdrant point ID."""
    return str(uuid.UUID(sha256[:32]))
