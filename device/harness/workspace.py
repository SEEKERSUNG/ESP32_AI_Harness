import os

from .config import join_path, read_text, write_text
from .executor import forget_workspace_module


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root="/", config=None, clean_tmp=False):
        self.config = config
        self.root = join_path(root, "workspace")
        self.tmp = self.root + "/tmp"
        self._mkdir(self.root)
        self._mkdir(self.tmp)
        # /workspace/tmp survives reboots and deploys: the model often keeps
        # reusing a stored script across turns. Cleanup is explicit only
        # (/workspace clean or Workspace(..., clean_tmp=True)).
        if clean_tmp:
            self.clean_tmp()

    def _limit(self, name, default, minimum=1, maximum=1024 * 1024):
        value = self.config.get(name, default) if self.config is not None else default
        try:
            value = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(value, maximum))

    def file_limit(self):
        return self._limit("workspace_file_limit", 32768)

    def total_limit(self):
        return self._limit("workspace_total_limit", 262144)

    def files_limit(self):
        return self._limit("workspace_files_limit", 64)

    def read_limit(self):
        return self._limit("workspace_read_limit", 16384)

    def source_limit(self):
        return self.file_limit()

    def list_limit(self):
        return self._limit("workspace_list_limit", 100)

    @staticmethod
    def _mkdir(path):
        try:
            os.mkdir(path)
        except OSError:
            pass

    def _parts(self, relative, allow_empty=True):
        if relative is None:
            relative = ""
        if not isinstance(relative, str):
            raise WorkspaceError("Workspace path must be a string")
        relative = relative.strip()
        if relative == "/workspace":
            relative = ""
        elif relative.startswith("/workspace/"):
            relative = relative[len("/workspace/"):]
        elif relative.startswith("/"):
            raise WorkspaceError("Workspace paths must be relative or start with /workspace")
        parts = []
        for part in relative.split("/"):
            if part in ("", "."):
                continue
            if part == ".." or "\\" in part:
                raise WorkspaceError("Workspace path may not traverse outside /workspace")
            if len(part) > 64:
                raise WorkspaceError("Workspace path component is too long")
            for char in part:
                if not ("a" <= char <= "z" or "A" <= char <= "Z"
                        or "0" <= char <= "9" or char in "_-."):
                    raise WorkspaceError("Workspace path contains an invalid character")
            parts.append(part)
        if len(parts) > 4:
            raise WorkspaceError("Workspace path is too deep")
        if not parts and not allow_empty:
            raise WorkspaceError("Workspace path is empty")
        return parts

    def path(self, relative, allow_empty=True):
        parts = self._parts(relative, allow_empty)
        path = self.root
        for part in parts:
            path = path + "/" + part
        return path, "/".join(parts)

    def display(self, relative):
        relative = relative.strip("/")
        return "/workspace" if not relative else "/workspace/" + relative

    def _ensure_parent(self, parts):
        path = self.root
        for part in parts[:-1]:
            path = path + "/" + part
            self._mkdir(path)

    def _is_dir(self, path):
        try:
            os.listdir(path)
            return True
        except OSError:
            return False

    def stat(self, relative):
        path, clean = self.path(relative)
        if not clean:
            return {"path": "/workspace", "kind": "dir", "size": 0}
        try:
            size = os.stat(path)[6]
        except OSError:
            raise WorkspaceError("Workspace path not found: %s" % self.display(clean))
        is_dir = self._is_dir(path)
        return {
            "path": self.display(clean),
            "kind": "dir" if is_dir else "file",
            "size": 0 if is_dir else size,
        }

    def list(self, relative="", limit=None):
        path, clean = self.path(relative)
        if not self._is_dir(path):
            raise WorkspaceError("Not a workspace directory: %s" % self.display(clean))
        limit = min(limit or self.list_limit(), self.list_limit())
        entries = []
        try:
            names = os.listdir(path)
        except OSError:
            return entries
        for name in names[:limit]:
            child = path + "/" + name
            child_relative = name if not clean else clean + "/" + name
            is_dir = self._is_dir(child)
            size = 0
            if not is_dir:
                try:
                    size = os.stat(child)[6]
                except OSError:
                    continue
            entries.append({
                "path": self.display(child_relative),
                "kind": "dir" if is_dir else "file",
                "size": size,
            })
        return sorted(entries, key=lambda item: item["path"])

    def _read_capped(self, relative, limit):
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            raise WorkspaceError("Cannot read a directory: %s" % self.display(clean))
        try:
            text = read_text(path)
        except OSError:
            raise WorkspaceError("Workspace file not found: %s" % self.display(clean))
        limit = max(1, int(limit))
        truncated = len(text) > limit
        return {
            "path": self.display(clean),
            "text": text[:limit],
            "chars": len(text),
            "truncated": truncated,
        }

    def read(self, relative, limit=None):
        cap = self.read_limit()
        if limit is not None:
            cap = min(int(limit), cap)
        return self._read_capped(relative, cap)

    def read_source(self, relative, limit=None):
        """Read a complete script for execution, not a display-truncated view."""
        cap = self.source_limit()
        if limit is not None:
            cap = min(int(limit), cap)
        return self._read_capped(relative, cap)

    def _walk(self, path, files):
        try:
            names = os.listdir(path)
        except OSError:
            return
        for name in names:
            child = path + "/" + name
            if self._is_dir(child):
                self._walk(child, files)
            else:
                try:
                    files.append(os.stat(child)[6])
                except OSError:
                    pass

    def totals(self):
        files = []
        self._walk(self.root, files)
        return len(files), sum(files)

    def write(self, relative, value, overwrite=True):
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            raise WorkspaceError("Cannot write a directory: %s" % self.display(clean))
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode()
            except ValueError:
                raise WorkspaceError("Workspace files must be UTF-8 text")
        else:
            value = str(value)
        if len(value) > self.file_limit():
            raise WorkspaceError("Workspace file exceeds %s characters" % self.file_limit())
        exists = os.path.exists(path) if hasattr(os, "exists") else self._file_exists(path)
        if exists and not overwrite:
            raise WorkspaceError("Workspace file exists: %s" % self.display(clean))
        current = 0
        try:
            current = os.stat(path)[6]
        except OSError:
            pass
        count, total = self.totals()
        if not exists and count >= self.files_limit():
            raise WorkspaceError("Workspace exceeds %s files" % self.files_limit())
        if total - current + len(value) > self.total_limit():
            raise WorkspaceError("Workspace exceeds %s characters" % self.total_limit())
        self._ensure_parent(self._parts(clean, allow_empty=False))
        write_text(path, value)
        forget_workspace_module(path)
        return {"path": self.display(clean), "chars": len(value)}

    def append(self, relative, value):
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            raise WorkspaceError(
                "Cannot append to a directory: %s" % self.display(clean))
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode()
            except ValueError:
                raise WorkspaceError("Workspace files must be UTF-8 text")
        else:
            value = str(value)
        exists = (os.path.exists(path) if hasattr(os, "exists")
                  else self._file_exists(path))
        existing = ""
        if exists:
            bundle = self.read_source(relative)
            if bundle["truncated"]:
                raise WorkspaceError(
                    "Workspace file exceeds %s characters"
                    % self.source_limit())
            existing = bundle["text"]
        return self.write(relative, existing + value, overwrite=True)

    def replace(self, relative, match, value, all=False):
        if not isinstance(match, str) or not match:
            raise WorkspaceError("match must be non-empty text")
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode()
            except ValueError:
                raise WorkspaceError("Workspace files must be UTF-8 text")
        else:
            value = str(value)
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            raise WorkspaceError(
                "Cannot replace in a directory: %s" % self.display(clean))
        bundle = self.read_source(relative)
        if bundle["truncated"]:
            raise WorkspaceError(
                "Workspace file exceeds %s characters" % self.source_limit())
        text = bundle["text"]
        count = text.count(match)
        if count == 0:
            raise WorkspaceError(
                "match not found in %s" % self.display(clean))
        if not all and count != 1:
            raise WorkspaceError(
                "match occurs %s times in %s; make it unique or set all=true"
                % (count, self.display(clean)))
        updated = (text.replace(match, value) if all
                   else text.replace(match, value, 1))
        self.write(relative, updated, overwrite=True)
        return {
            "path": self.display(clean),
            "occurrences": count if all else 1,
            "chars": len(updated),
        }

    def write_bytes(self, relative, value, overwrite=True, limit=None):
        """Write raw bytes, used by the HTTP download tool.

        The default per-file limit is the workspace text limit; callers may
        pass a larger limit (still bounded by the workspace total).
        """
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            raise WorkspaceError("Cannot write a directory: %s" % self.display(clean))
        data = bytes(value)
        maximum = max(1, int(limit or self.file_limit()))
        if len(data) > maximum:
            raise WorkspaceError("File exceeds %s bytes" % maximum)
        exists = os.path.exists(path) if hasattr(os, "exists") else self._file_exists(path)
        if exists and not overwrite:
            raise WorkspaceError("Workspace file exists: %s" % self.display(clean))
        current = 0
        try:
            current = os.stat(path)[6]
        except OSError:
            pass
        count, total = self.totals()
        if not exists and count >= self.files_limit():
            raise WorkspaceError("Workspace exceeds %s files" % self.files_limit())
        if total - current + len(data) > self.total_limit():
            raise WorkspaceError("Workspace exceeds %s bytes" % self.total_limit())
        self._ensure_parent(self._parts(clean, allow_empty=False))
        temporary = path + ".tmp"
        with open(temporary, "wb") as handle:
            handle.write(data)
        try:
            os.rename(temporary, path)
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass
            os.rename(temporary, path)
        forget_workspace_module(path)
        return {"path": self.display(clean), "bytes": len(data)}

    @staticmethod
    def _file_exists(path):
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def mkdir(self, relative):
        path, clean = self.path(relative, allow_empty=False)
        if self._file_exists(path) and not self._is_dir(path):
            raise WorkspaceError("A file exists at: %s" % self.display(clean))
        self._ensure_parent(self._parts(clean, allow_empty=False))
        self._mkdir(path)
        return {"path": self.display(clean), "kind": "dir"}

    def remove(self, relative):
        path, clean = self.path(relative, allow_empty=False)
        if self._is_dir(path):
            try:
                os.rmdir(path)
            except OSError:
                raise WorkspaceError("Workspace directory is not empty: %s" % self.display(clean))
            return {"path": self.display(clean), "removed": True}
        try:
            os.remove(path)
        except OSError:
            raise WorkspaceError("Workspace file not found: %s" % self.display(clean))
        forget_workspace_module(path)
        return {"path": self.display(clean), "removed": True}

    def _remove_tree(self, path):
        try:
            names = os.listdir(path)
        except OSError:
            return
        for name in names:
            child = path + "/" + name
            if self._is_dir(child):
                self._remove_tree(child)
            else:
                try:
                    os.remove(child)
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except OSError:
            pass

    def clean_tmp(self):
        self._remove_tree(self.tmp)
        self._mkdir(self.tmp)
        return self.summary()

    def summary(self):
        count, total = self.totals()
        tmp_count = 0
        tmp_total = 0
        try:
            for name in os.listdir(self.tmp):
                path = self.tmp + "/" + name
                if self._is_dir(path):
                    files = []
                    self._walk(path, files)
                    tmp_count += len(files)
                    tmp_total += sum(files)
                else:
                    tmp_count += 1
                    tmp_total += os.stat(path)[6]
        except OSError:
            pass
        return {
            "root": "/workspace",
            "tmp": "/workspace/tmp",
            "files": count,
            "chars": total,
            "tmp_files": tmp_count,
            "tmp_chars": tmp_total,
            "file_limit": self.file_limit(),
            "total_limit": self.total_limit(),
            "files_limit": self.files_limit(),
        }
