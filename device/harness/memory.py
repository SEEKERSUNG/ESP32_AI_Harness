from .config import join_path, read_text, write_text


class MemoryError(Exception):
    pass


TEMPLATE = (
    "# Project Memory\n\n"
    "<!-- Always-loaded project context. Keep this concise. -->\n"
)


class MemoryStore:
    def __init__(self, root="/", config=None):
        self.config = config
        self.path = join_path(root, "MEMORY.md")
        try:
            read_text(self.path)
        except OSError:
            write_text(self.path, TEMPLATE)

    def limit(self):
        value = self.config.get("memory_char_limit", 8192) if self.config is not None else 8192
        try:
            return max(256, int(value))
        except (TypeError, ValueError):
            return 8192

    def append_limit(self):
        value = self.config.get("memory_append_limit", 2048) if self.config is not None else 2048
        try:
            return max(32, int(value))
        except (TypeError, ValueError):
            return 2048

    def load(self):
        try:
            text = read_text(self.path)
        except OSError:
            text = TEMPLATE
        return text

    def bundle(self):
        text = self.load()
        limit = self.limit()
        truncated = len(text) > limit
        return {
            "content": text[:limit],
            "chars": len(text),
            "truncated": truncated,
            "limit": limit,
        }

    def append(self, text):
        text = str(text or "").strip()
        if not text:
            raise MemoryError("Memory append is empty")
        if len(text) > self.append_limit():
            raise MemoryError("Memory append exceeds %s characters" % self.append_limit())
        current = self.load()
        if len(current) + len(text) + 1 > self.limit():
            raise MemoryError("Memory exceeds %s characters" % self.limit())
        if not current.endswith("\n"):
            current += "\n"
        write_text(self.path, current + text + "\n")
        return {"chars": len(text), "total": len(current) + len(text) + 1}

    def matching(self, text):
        match = self._line_text(text, "Memory match")
        return [line for line in self.load().splitlines() if match in line]

    def delete(self, text):
        match = self._line_text(text, "Memory match")
        current = self.load()
        lines = current.splitlines()
        kept = [line for line in lines if match not in line]
        removed = len(lines) - len(kept)
        if not removed:
            raise MemoryError("No memory line contains: %s" % match)
        updated = self._join_lines(kept, current)
        write_text(self.path, updated)
        return {"removed": removed, "total": len(updated)}

    def replace(self, match, text):
        match = self._line_text(match, "Memory match")
        replacement = self._line_text(text, "Memory replacement")
        current = self.load()
        lines = current.splitlines()
        indexes = [index for index, line in enumerate(lines) if match in line]
        if not indexes:
            raise MemoryError("No memory line contains: %s" % match)
        if len(indexes) > 1:
            raise MemoryError(
                "%s memory lines contain '%s'; use a more specific match"
                % (len(indexes), match)
            )
        index = indexes[0]
        previous = lines[index]
        lines[index] = replacement
        updated = self._join_lines(lines, current)
        if len(updated) > self.limit():
            raise MemoryError("Memory exceeds %s characters" % self.limit())
        write_text(self.path, updated)
        return {"before": previous, "after": replacement, "total": len(updated)}

    def clear(self):
        write_text(self.path, TEMPLATE)
        return TEMPLATE

    def _line_text(self, value, name):
        text = str(value or "").strip()
        if not text:
            raise MemoryError("%s is empty" % name)
        if "\n" in text or "\r" in text:
            raise MemoryError("%s must be one line" % name)
        if len(text) > self.append_limit():
            raise MemoryError("%s exceeds %s characters" % (name, self.append_limit()))
        return text

    @staticmethod
    def _join_lines(lines, original):
        text = "\n".join(lines)
        if text and original.endswith("\n"):
            text += "\n"
        return text
