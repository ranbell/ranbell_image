_PYTHON_SORT: dict[str, tuple] = {
    "newest":      (lambda d: d.get("mtime", ""), True),
    "oldest":      (lambda d: d.get("mtime", ""), False),
    "name_asc":    (lambda d: (d.get("name") or "").lower(), False),
    "name_desc":   (lambda d: (d.get("name") or "").lower(), True),
    "size_desc":   (lambda d: d.get("size", 0), True),
    "size_asc":    (lambda d: d.get("size", 0), False),
    "rating_desc": (lambda d: d.get("star_rating") or 0, True),
}


def sort_docs(docs: list[dict], sort: str) -> list[dict]:
    if sort not in _PYTHON_SORT:
        return docs
    key_fn, reverse = _PYTHON_SORT[sort]
    return sorted(docs, key=key_fn, reverse=reverse)
