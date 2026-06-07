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
