from .config import join_path, read_json, read_text, remove_file, write_json, write_text


class Recovery:
    def __init__(self, root="/"):
        self.marker_path = join_path(root, "recovery.json")
        self.source_path = join_path(root, "recovery.py")

    def begin(self, source):
        write_text(self.source_path, source)
        write_json(self.marker_path, {"running": True, "size": len(source)})

    def end(self):
        remove_file(self.marker_path)
        remove_file(self.source_path)

    def consume(self):
        metadata = read_json(self.marker_path, {})
        if not metadata.get("running"):
            return None
        try:
            source = read_text(self.source_path)
        except OSError:
            source = ""
        remove_file(self.marker_path)
        remove_file(self.source_path)
        return source

    def feed(self):
        pass


class Supervisor:
    def __init__(self, timeout_ms=0):
        self._watchdog = None
        if timeout_ms:
            from machine import WDT
            self._watchdog = WDT(timeout=timeout_ms)

    def feed(self):
        if self._watchdog is not None:
            self._watchdog.feed()
