"""A named caller's files: the backend of the strict build (feature 009, FR-009a, research R16).

Every thread works in the one workspace today. A thread a named caller started over A2A works
in a directory of that caller's own, ``<files dir>/<caller>``, and reads the skills of the
workspace without being able to change them. The local caller, every agent that calls on
the service's own address without a token, works in the shared workspace, as a thread
started through the service's own API does.

deepagents takes one backend object for the whole graph, its subagent included, so the
choice is made per call: ``CallerFiles`` reads ``configurable["freyja_caller_files"]`` from
the run's config, which ``SegmentedGraph`` puts there for every run of a caller's thread, and
hands the call to the backend that key names. A call made without the key is refused: the
strict build never falls back to the shared workspace by omission.

There is no shell here: neither the workspace's backend nor this one runs commands, so the
``execute`` tool answers with an error on every thread.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileUploadResponse,
    WriteResult,
)
from langgraph.config import get_config

# The key of the run's config that names whose files a run works in: "" for the local
# caller, the caller's name for a named one.
FILES_KEY = "freyja_caller_files"
SKILLS_ROUTE = "/skills/"
# A caller's name is a directory name: the form of an agent's name (FR-004), so that no name
# leads out of the files directory.
CALLER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
READ_ONLY = "The skills are read-only here: nothing under /skills/ can be changed."


class NoFilesError(RuntimeError):
    """A file operation of the strict build ran without the key that says whose files."""


class ReadOnlyFiles(BackendProtocol):
    """``inner`` for reading; every write, edit, delete, and upload is refused."""

    def __init__(self, inner: BackendProtocol) -> None:
        self.inner = inner

    def ls(self, path: str) -> Any:
        return self.inner.ls(path)

    async def als(self, path: str) -> Any:
        return await self.inner.als(path)

    def read(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return self.inner.read(file_path, *args, **kwargs)

    async def aread(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return await self.inner.aread(file_path, *args, **kwargs)

    def grep(self, pattern: str, *args: Any, **kwargs: Any) -> Any:
        return self.inner.grep(pattern, *args, **kwargs)

    async def agrep(self, pattern: str, *args: Any, **kwargs: Any) -> Any:
        return await self.inner.agrep(pattern, *args, **kwargs)

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return self.inner.glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return await self.inner.aglob(pattern, path)

    def download_files(self, paths: list[str]) -> Any:
        return self.inner.download_files(paths)

    async def adownload_files(self, paths: list[str]) -> Any:
        return await self.inner.adownload_files(paths)

    def write(self, *_: Any, **__: Any) -> WriteResult:
        return WriteResult(error=READ_ONLY)

    async def awrite(self, *_: Any, **__: Any) -> WriteResult:
        return WriteResult(error=READ_ONLY)

    def edit(self, *_: Any, **__: Any) -> EditResult:
        return EditResult(error=READ_ONLY)

    async def aedit(self, *_: Any, **__: Any) -> EditResult:
        return EditResult(error=READ_ONLY)

    def delete(self, *_: Any, **__: Any) -> DeleteResult:
        return DeleteResult(error=READ_ONLY)

    async def adelete(self, *_: Any, **__: Any) -> DeleteResult:
        return DeleteResult(error=READ_ONLY)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)


class CallerFiles(BackendProtocol):
    """The strict build's backend: per call, the files of the caller the run is for."""

    def __init__(self, shared: BackendProtocol, files_dir: str | Path, skills_dir: str | Path):
        self.shared = shared
        self.files_dir = Path(files_dir)
        self.skills = ReadOnlyFiles(FilesystemBackend(root_dir=skills_dir, virtual_mode=True))
        self._areas: dict[str, BackendProtocol] = {}

    def area(self, caller: str) -> BackendProtocol:
        """The backend of ``caller``'s area, kept once made. Its directory is made by the
        first write there, so that nothing here touches the disk."""
        found = self._areas.get(caller)
        if found is None:
            found = CompositeBackend(
                default=FilesystemBackend(root_dir=self.files_dir / caller, virtual_mode=True),
                routes={SKILLS_ROUTE: self.skills},
            )
            self._areas[caller] = found
        return found

    def _for(self) -> BackendProtocol:
        try:
            configurable = get_config().get("configurable", {})
        except RuntimeError:  # outside a run there is no config, and no caller to act for
            configurable = {}
        caller = configurable.get(FILES_KEY)
        if caller is None:
            raise NoFilesError(
                "A file operation of a caller's thread ran without saying whose files: "
                "nothing was read or written."
            )
        if caller == "":
            return self.shared
        if not isinstance(caller, str) or not CALLER_NAME.fullmatch(caller):
            raise NoFilesError("A caller's name that is not a directory name: nothing was read.")
        return self.area(caller)

    def ls(self, path: str) -> Any:
        return self._for().ls(path)

    async def als(self, path: str) -> Any:
        return await self._for().als(path)

    def read(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return self._for().read(file_path, *args, **kwargs)

    async def aread(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return await self._for().aread(file_path, *args, **kwargs)

    def grep(self, pattern: str, *args: Any, **kwargs: Any) -> Any:
        return self._for().grep(pattern, *args, **kwargs)

    async def agrep(self, pattern: str, *args: Any, **kwargs: Any) -> Any:
        return await self._for().agrep(pattern, *args, **kwargs)

    def glob(self, pattern: str, path: str | None = None) -> Any:
        return self._for().glob(pattern, path)

    async def aglob(self, pattern: str, path: str | None = None) -> Any:
        return await self._for().aglob(pattern, path)

    def write(self, file_path: str, content: str) -> Any:
        return self._for().write(file_path, content)

    async def awrite(self, file_path: str, content: str) -> Any:
        return await self._for().awrite(file_path, content)

    def edit(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return self._for().edit(file_path, *args, **kwargs)

    async def aedit(self, file_path: str, *args: Any, **kwargs: Any) -> Any:
        return await self._for().aedit(file_path, *args, **kwargs)

    def delete(self, file_path: str) -> Any:
        return self._for().delete(file_path)

    async def adelete(self, file_path: str) -> Any:
        return await self._for().adelete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return self._for().upload_files(files)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        return await self._for().aupload_files(files)

    def download_files(self, paths: list[str]) -> Any:
        return self._for().download_files(paths)

    async def adownload_files(self, paths: list[str]) -> Any:
        return await self._for().adownload_files(paths)
