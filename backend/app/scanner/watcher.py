import asyncio
import logging
import os
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .walk import is_image_name

logger = logging.getLogger(__name__)


class _ImageEventHandler(FileSystemEventHandler):
    """Bridge watchdog thread events to an asyncio queue.

    Two kinds go on the queue. ("created", file) is an image that finished being
    written — it may be one the app saved itself. ("dir", directory) is any other
    change in that directory: something removed, renamed, moved in or out,
    a folder created or deleted. Listing that one directory is enough to see it;
    the scanner finds new subfolders and lost ones from the listing.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue

    def _put(self, event_type: str, path: Path) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (event_type, path))

    def _parent(self, path: str) -> None:
        self._put("dir", Path(os.path.dirname(path)))

    def on_closed(self, event) -> None:
        if not event.is_directory and is_image_name(event.src_path):
            self._put("created", Path(event.src_path))

    def on_created(self, event) -> None:
        # A folder made, or moved in from outside the tree. Files copied into
        # it afterwards each arrive as on_closed.
        if event.is_directory:
            self._parent(event.src_path)

    def on_deleted(self, event) -> None:
        if event.is_directory or is_image_name(event.src_path):
            self._parent(event.src_path)

    def on_moved(self, event) -> None:
        # A rename touches both directories. Temp-file-then-rename (syncthing,
        # most editors) only has an image name on the destination side.
        if event.is_directory or is_image_name(event.src_path) or is_image_name(event.dest_path):
            self._parent(event.src_path)
            self._parent(event.dest_path)


class ImageDirectoryWatcher:
    """Watch source_images_dir and generated_images_dir and hand what changed
    to the scanner, as directories to reconcile (a scan_sync job).

    - A new file in generated_images_dir is usually one save_generated_image()
      already registered; those are left out. AI_PIPELINE tagging is still
      submitted for new non-invoke files.
    - Anything else becomes "list this directory again" — never a whole-library
      heal. While one scan_sync is waiting to start, more directories join it
      instead of queueing another.
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

    def _submit_sync(self, dirs: set[str]) -> None:
        from ..jobs.runners import run_scan_sync
        from ..spooler.models import JobLane
        from .scanner import request_sync

        if dirs and request_sync(dirs):
            self._spooler.submit(
                JobLane.SYNC,
                "scan_sync",
                run_scan_sync,
                db=self._db,
                ollama=self._ollama,
                spooler=self._spooler,
            )
            logger.info("Auto-triggered scan_sync for %d folder(s)", len(dirs))

    async def _dispatch_loop(self) -> None:
        from ..jobs.runners import run_pipeline_tagging
        from ..spooler.models import JobLane
        from .scanner import consume_self_registered, wait_for_registration

        # Source and generated keep separate debounces: the app writes to
        # generated in bursts it registers itself, and answers fast there.
        source_dirs: set[str] = set()
        source_deadline: float | None = None
        generated_dirs: set[str] = set()
        generated_files: set[Path] = set()
        generated_deadline: float | None = None
        has_non_invoke = False

        while True:
            try:
                event_type, path = await asyncio.wait_for(
                    self._event_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                now = asyncio.get_event_loop().time()
                if source_deadline is not None and now >= source_deadline:
                    self._submit_sync(source_dirs)
                    source_dirs = set()
                    source_deadline = None
                if generated_deadline is not None and now >= generated_deadline:
                    for p in generated_files:
                        # Registration typically finishes well under the 2s
                        # debounce, so this rarely actually waits.
                        await wait_for_registration(p)
                        if not consume_self_registered(p):
                            generated_dirs.add(str(p.parent))
                    self._submit_sync(generated_dirs)
                    if self._auto_ai_pipeline and has_non_invoke:
                        self._spooler.submit(
                            JobLane.TAGGING,
                            "ai_tagging_auto",
                            run_pipeline_tagging,
                            db=self._db,
                            ollama=self._ollama,
                            spooler=self._spooler,
                        )
                    generated_dirs = set()
                    generated_files = set()
                    generated_deadline = None
                    has_non_invoke = False
                continue
            except asyncio.CancelledError:
                break

            try:
                if path.is_relative_to(self._generated_dir):
                    generated_deadline = asyncio.get_event_loop().time() + 2.0
                    if event_type == "created":
                        # Invoke-generated images are saved under generated_dir/invoke/
                        # and are managed by the invoke pipeline (wd14 + alignment jobs).
                        # Debounce all generated files; skip ai_pipeline_auto for invoke ones.
                        generated_files.add(path)
                        if not path.is_relative_to(self._generated_dir / "invoke"):
                            has_non_invoke = True
                    else:
                        # Removed / renamed / moved: no self-registration record
                        # covers that, so the directory is always reconciled.
                        generated_dirs.add(str(path))
                else:
                    source_dirs.add(str(path.parent) if event_type == "created" else str(path))
                    source_deadline = asyncio.get_event_loop().time() + self._debounce
            except Exception as e:
                logger.warning("Watcher dispatch error for %s: %s", path, e)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._observer:
            self._observer.stop()
            self._observer.join()
        logger.info("File watcher stopped")
