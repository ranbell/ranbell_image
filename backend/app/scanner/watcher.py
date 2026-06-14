import asyncio
import logging
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class _ImageEventHandler(FileSystemEventHandler):
    """Bridge watchdog thread events to an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue

    def _put(self, event_type: str, path: Path) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (event_type, path))

    def on_closed(self, event) -> None:
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTS:
            self._put("created", Path(event.src_path))

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._put("deleted", Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory and Path(event.dest_path).suffix.lower() in SUPPORTED_EXTS:
            self._put("moved", Path(event.dest_path))


class ImageDirectoryWatcher:
    """Watch source_images_dir and generated_images_dir and auto-submit jobs.

    - New file in generated_images_dir → register_image() + submit AI_PIPELINE job
    - Change in source_images_dir → submit SCAN_HEAL job after debounce
    """

    def __init__(
        self,
        db,
        ollama,
        spooler,
        debounce_seconds: float = 5.0,
        auto_ai_pipeline: bool = True,
    ) -> None:
        self._db = db
        self._ollama = ollama
        self._spooler = spooler
        self._debounce = debounce_seconds
        self._auto_ai_pipeline = auto_ai_pipeline
        self._observer: Observer | None = None
        self._task: asyncio.Task | None = None
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def start(self, source_dir: Path, generated_dir: Path) -> None:
        loop = asyncio.get_event_loop()
        handler = _ImageEventHandler(loop, self._event_queue)

        self._observer = Observer()
        if source_dir.exists():
            self._observer.schedule(handler, str(source_dir), recursive=True)
            logger.info("Watching source: %s", source_dir)
        if generated_dir.exists():
            self._observer.schedule(handler, str(generated_dir), recursive=True)
            logger.info("Watching generated: %s", generated_dir)

        self._observer.start()
        self._generated_dir = generated_dir
        self._task = asyncio.create_task(self._dispatch_loop())
        logger.info("File watcher started")

    async def _dispatch_loop(self) -> None:
        from ..jobs.runners import run_pipeline, run_scan_heal
        from ..spooler.models import JobLane
        from .scanner import register_image, wait_for_registration

        pending_heal = False
        heal_deadline: float | None = None

        while True:
            try:
                event_type, path = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                if pending_heal and heal_deadline is not None:
                    now = asyncio.get_event_loop().time()
                    if now >= heal_deadline:
                        self._spooler.submit(
                            JobLane.SYNC,
                            "scan_heal",
                            run_scan_heal,
                            db=self._db,
                            ollama=self._ollama,
                            spooler=self._spooler,
                        )
                        logger.info("Auto-triggered SCAN_HEAL")
                        pending_heal = False
                        heal_deadline = None
                continue
            except asyncio.CancelledError:
                break

            try:
                if path.is_relative_to(self._generated_dir):
                    if event_type == "created":
                        # Invoke-generated images are saved under generated_dir/invoke/
                        # and are managed by the invoke pipeline (wd14 + alignment jobs).
                        # Skip ai_pipeline_auto for these to avoid redundant scans on the main screen.
                        is_invoke = path.is_relative_to(self._generated_dir / "invoke")

                        sha256 = await register_image(path, self._db)
                        if not sha256:
                            await wait_for_registration(path)
                            logger.debug("waited for in-flight registration: %s", path.name)
                        logger.info("Auto-registered%s: %s", " (invoke-skip-pipeline)" if is_invoke else "", path.name)
                        if self._auto_ai_pipeline and not is_invoke:
                            self._spooler.submit(
                                JobLane.EMBEDDING,
                                "ai_pipeline_auto",
                                run_pipeline,
                                db=self._db,
                                ollama=self._ollama,
                                spooler=self._spooler,
                            )
                else:
                    pending_heal = True
                    heal_deadline = asyncio.get_event_loop().time() + self._debounce
            except Exception as e:
                logger.warning("Watcher dispatch error for %s: %s", path, e)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._observer:
            self._observer.stop()
            self._observer.join()
        logger.info("File watcher stopped")
