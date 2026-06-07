import io
from PIL import Image


def _compute_grid(n: int) -> tuple[int, int]:
    if n <= 1:
        return 1, 1
    elif n == 2:
        return 2, 1
    elif n <= 4:
        return 2, 2
    else:
        return 3, 2  # up to 6 images


def create_tile_image(image_bytes_list: list[bytes], max_size: int = 1024) -> bytes:
    if not image_bytes_list:
        return b""

    cols, rows = _compute_grid(len(image_bytes_list))
    cell_w = max_size // cols
    cell_h = max_size // rows

    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (0, 0, 0))

    for i, img_bytes in enumerate(image_bytes_list[: cols * rows]):
        try:
            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except Exception:
            continue

        img.thumbnail((cell_w, cell_h), Image.LANCZOS)
        col = i % cols
        row = i // cols
        x = col * cell_w + (cell_w - img.width) // 2
        y = row * cell_h + (cell_h - img.height) // 2
        canvas.paste(img, (x, y))

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
