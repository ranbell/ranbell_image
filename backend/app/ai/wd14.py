import asyncio
import logging
from pathlib import Path
import numpy as np
from PIL import Image
from ..config import settings

logger = logging.getLogger(__name__)

MODEL_INPUT_SIZE = 448  # EVA02-large

_session = None
_tags_df = None


_loaded_model_dir: str | None = None
_token_index: dict[str, list[str]] | None = None
_cand_token_lengths: dict[str, int] | None = None
_tag_set_space: frozenset[str] | None = None


def _build_token_index() -> None:
    """Build inverted token index and tag vocab set for BM25 matching. Called once during model load."""
    global _token_index, _cand_token_lengths, _tag_set_space
    if _tags_df is None or _token_index is not None:
        return
    from ..alignment.bm25_matcher import tokenize
    idx: dict[str, list[str]] = {}
    lengths: dict[str, int] = {}
    for name in _tags_df["name"]:
        toks = tokenize(name)
        lengths[name] = len(toks)
        for tok in toks:
            idx.setdefault(tok, []).append(name)
    _token_index = idx
    _cand_token_lengths = lengths
    _tag_set_space = frozenset(name.replace("_", " ") for name in _tags_df["name"])
    logger.info("WD14 BM25 token index built (%d tokens, %d tags)", len(idx), len(_tag_set_space))


def bm25_find_tag(concept: str) -> str | None:
    """Return best-matching Danbooru tag name (underscore format) for a free-form concept."""
    from ..alignment.bm25_matcher import tokenize
    if _token_index is None:
        _build_token_index()
    if not _token_index:
        return None
    tokens = tokenize(concept)
    if not tokens:
        return None
    scores: dict[str, int] = {}
    for tok in tokens:
        for cand in _token_index.get(tok, []):
            scores[cand] = scores.get(cand, 0) + 1
    if not scores:
        return None
    n_tokens = len(tokens)
    best, best_j = None, 0.0
    for cand, overlap in scores.items():
        cand_len = _cand_token_lengths[cand]
        j = overlap / (n_tokens + cand_len - overlap)
        if j > best_j:
            best_j, best = j, cand
    return best if best_j >= 0.35 else None


def normalize_tag_string(text: str) -> str:
    """Normalize a comma-separated Danbooru tag string using BM25 vocab matching.

    Strips weight syntax like (tag:1.3), validates each token against selected_tags.csv,
    and maps near-matches via BM25. Returns space-style comma-separated tags.
    Unknown tokens with no BM25 match are dropped.
    """
    if not text or _tags_df is None:
        return text
    vocab = _tag_set_space or frozenset(name.replace("_", " ") for name in _tags_df["name"])
    result: list[str] = []
    seen: set[str] = set()
    for raw in (t.strip() for t in text.split(",") if t.strip()):
        # strip SDXL weight syntax: (tag:1.3) → tag, preserving ":" in tag names
        stripped = raw.strip("() ")
        left, sep, right = stripped.rpartition(":")
        tag = left.strip() if sep and right.rstrip(")").replace(".", "").isdigit() else stripped
        tag_space = tag.replace("_", " ").lower()
        if tag_space in vocab:
            if tag_space not in seen:
                result.append(tag_space)
                seen.add(tag_space)
        else:
            matched = bm25_find_tag(tag)
            if matched:
                key = matched.replace("_", " ")
                if key not in seen:
                    result.append(key)
                    seen.add(key)
    return ", ".join(result)


def _load_model(model_dir: str) -> None:
    global _session, _tags_df, _loaded_model_dir
    if _session is not None and _loaded_model_dir == model_dir:
        return

    import onnxruntime as ort
    import pandas as pd

    p = Path(model_dir)
    model_path = p / "model.onnx"
    tags_path = p / "selected_tags.csv"

    if not model_path.exists():
        raise FileNotFoundError(f"WD14 model not found: {model_path}")
    if not tags_path.exists():
        raise FileNotFoundError(f"WD14 tags not found: {tags_path}")

    logger.info("Loading WD14 model from %s", p)
    _session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    _tags_df = pd.read_csv(tags_path)
    _loaded_model_dir = model_dir
    logger.info("WD14 model loaded (%d tags)", len(_tags_df))
    _build_token_index()


def _predict_sync(image_path: Path, threshold: float, model_dir: str) -> list[str]:
    _load_model(model_dir)

    with Image.open(image_path) as img:
        img = img.convert("RGB").resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE), Image.BICUBIC)

    arr = np.array(img, dtype=np.float32)
    arr = arr[:, :, ::-1]          # RGB → BGR
    arr = np.expand_dims(arr, 0)

    input_name = _session.get_inputs()[0].name
    probs = _session.run(None, {input_name: arr})[0][0]

    return [
        _tags_df.iloc[i]["name"].replace("_", " ")
        for i, p in enumerate(probs)
        if p >= threshold
    ]


def _predict_sync_scored(
    image_path: Path, threshold: float, model_dir: str
) -> list[tuple[str, float]]:
    _load_model(model_dir)

    with Image.open(image_path) as img:
        img = img.convert("RGB").resize((MODEL_INPUT_SIZE, MODEL_INPUT_SIZE), Image.BICUBIC)

    arr = np.array(img, dtype=np.float32)
    arr = arr[:, :, ::-1]
    arr = np.expand_dims(arr, 0)

    input_name = _session.get_inputs()[0].name
    probs = _session.run(None, {input_name: arr})[0][0]

    results = [
        (_tags_df.iloc[i]["name"].replace("_", " "), float(p))
        for i, p in enumerate(probs)
        if p >= threshold
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


async def predict_tags(
    image_path: Path,
    threshold: float | None = None,
    model_dir: str | None = None,
) -> list[str]:
    t = threshold if threshold is not None else settings.wd14_threshold
    d = model_dir if model_dir is not None else settings.wd14_model_dir
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _predict_sync, image_path, t, d)


async def predict_tags_scored(
    image_path: Path,
    threshold: float | None = None,
    model_dir: str | None = None,
) -> list[tuple[str, float]]:
    t = threshold if threshold is not None else settings.wd14_threshold
    d = model_dir if model_dir is not None else settings.wd14_model_dir
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _predict_sync_scored, image_path, t, d)
