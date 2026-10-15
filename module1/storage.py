from pathlib import Path
import os
import re
import threading
import uuid


class StorageError(Exception):
    pass


class UnsafeFilename(StorageError):
    pass


class FileAlreadyExists(StorageError):
    pass


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(filename: str) -> str:
    if not isinstance(filename, str) or not filename or len(filename) > 255:
        raise UnsafeFilename("filename must be between 1 and 255 characters")
    if filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise UnsafeFilename("path separators are not allowed")
    if ".." in filename or _CONTROL_CHARACTERS.search(filename):
        raise UnsafeFilename("filename contains an unsafe sequence")
    return filename


class Storage:
    def __init__(self, root: str, max_file_size: int = 100 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file_size = max_file_size
        self._name_lock = threading.Lock()

    def path_for(self, filename: str) -> Path:
        name = safe_filename(filename)
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise UnsafeFilename("resolved path escapes storage root")
        return path

    def create_temp(self, filename: str) -> tuple[Path, Path]:
        final_path = self.path_for(filename)
        temp_path = self.root / f".{final_path.name}.{uuid.uuid4().hex}.part"
        return final_path, temp_path

    def finalize(self, final_path: Path, temp_path: Path) -> None:
        with self._name_lock:
            if final_path.exists():
                raise FileAlreadyExists("a file with that name already exists")
            try:
                os.replace(temp_path, final_path)
            except FileExistsError as exc:
                raise FileAlreadyExists("a file with that name already exists") from exc

    def list_files(self) -> list[dict]:
        files = []
        for path in sorted(self.root.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                files.append(
                    {
                        "file_id": path.name,
                        "filename": path.name,
                        "size": path.stat().st_size,
                    }
                )
        return files
