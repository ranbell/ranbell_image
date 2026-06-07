import time
from collections import Counter, defaultdict

_cooccurrence_cache: dict | None = None
_cache_ts: float = 0.0
CACHE_TTL = 300  # 5 minutes

TAG_CATEGORIES = ("subject", "style", "mood", "environment", "action", "quality")

CHUNK_SIZE = 50  # Number of tags to send to the LLM at once


async def build_tag_cooccurrence(db, min_count: int = 2, top_tags: int = 80) -> dict:
    """
    Build a tag co-occurrence graph from wd14_tags + prompts across all images.
    Results are cached in memory for 5 minutes.
    """
    global _cooccurrence_cache, _cache_ts

    now = time.monotonic()
    if _cooccurrence_cache is not None and (now - _cache_ts) < CACHE_TTL:
        # Cache hit: filter by min_count / top_tags and return
        return _filter_cooccurrence(_cooccurrence_cache, min_count, top_tags)

    docs = await db.scroll_tags()

    tag_counter: Counter = Counter()
    pair_counter: Counter = Counter()

    for doc in docs:
        tags: list[str] = list(doc.get("wd14_tags") or [])
        prompt = doc.get("positive_prompt") or ""
        # Split prompts by comma and treat each part as a tag
        for part in prompt.split(","):
            t = part.strip().lower()
            if t:
                tags.append(t)

        tags = list(dict.fromkeys(tags))  # Deduplicate while preserving order
        tag_counter.update(tags)

        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                pair = tuple(sorted([tags[i], tags[j]]))
                pair_counter[pair] += 1

    _cooccurrence_cache = {
        "tag_counter": tag_counter,
        "pair_counter": pair_counter,
    }
    _cache_ts = now

    return _filter_cooccurrence(_cooccurrence_cache, min_count, top_tags)


def _filter_cooccurrence(cache: dict, min_count: int, top_tags: int) -> dict:
    tag_counter: Counter = cache["tag_counter"]
    pair_counter: Counter = cache["pair_counter"]

    # Select top_tags most common tags
    top = {tag for tag, _ in tag_counter.most_common(top_tags)}

    nodes = [
        {"id": tag, "label": tag, "count": tag_counter[tag]}
        for tag in top
    ]

    edges = []
    seen_pairs: set = set()
    for (t1, t2), weight in pair_counter.items():
        if t1 not in top or t2 not in top:
            continue
        if weight < min_count:
            continue
        key = (t1, t2)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        edges.append({"source": t1, "target": t2, "weight": weight})

    # Limit edge count to a manageable range (top 300)
    edges.sort(key=lambda e: e["weight"], reverse=True)
    edges = edges[:300]

    return {"nodes": nodes, "edges": edges}


def invalidate_cache() -> None:
    global _cooccurrence_cache, _cache_ts
    _cooccurrence_cache = None
    _cache_ts = 0.0


def split_chunks(tags: list[str], size: int = CHUNK_SIZE) -> list[list[str]]:
    return [tags[i:i + size] for i in range(0, len(tags), size)]
