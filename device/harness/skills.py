import os

from .config import join_path, read_text, write_text


class SkillError(Exception):
    pass


SKILL_FILE = "SKILL.md"
MAX_DESCRIPTION = 200


def validate_name(name):
    if not name or len(name) > 32:
        raise SkillError("Name must contain 1-32 characters")
    if name.startswith("__"):
        raise SkillError("Names may not start with '__'")
    for char in name:
        if not ("a" <= char <= "z" or "A" <= char <= "Z"
                or "0" <= char <= "9" or char in "_-"):
            raise SkillError("Name may only contain letters, digits, '_' and '-'")
    return name


def _parse_frontmatter(text):
    """Parse the small YAML-like frontmatter used by SKILL.md."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return {}, text
    metadata = {}
    for line in lines[1:end]:
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        metadata[key.strip()] = value
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return metadata, body


def _skill_text(name, description, body):
    return "---\nname: %s\ndescription: %s\n---\n\n%s\n" % (
        name, description, body)


class SkillStore:
    def __init__(self, root="/"):
        self.root = join_path(root, "skills")
        try:
            os.mkdir(self.root)
        except OSError:
            pass

    def paths(self, name):
        validate_name(name)
        directory = join_path(self.root, name)
        return directory, join_path(directory, SKILL_FILE)

    def exists(self, name):
        _, path = self.paths(name)
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def load(self, name):
        _, path = self.paths(name)
        try:
            return read_text(path)
        except OSError:
            raise SkillError("Skill not found: %s" % name)

    def read_bundle(self, name, limit=16384):
        text = self.load(name)
        metadata, _ = _parse_frontmatter(text)
        content = text
        truncated = len(content) > limit
        if truncated:
            content = content[:limit]
        return {
            "name": metadata.get("name", name),
            "description": metadata.get("description", ""),
            "content": content,
            "chars": len(text),
            "truncated": truncated,
        }

    def save(self, name, description, body, limit=16384):
        validate_name(name)
        description = str(description or "").strip().replace("\n", " ")
        if not description:
            raise SkillError("Skill description is required")
        description = description[:MAX_DESCRIPTION]
        body = str(body or "")
        if len(body) > limit:
            raise SkillError("Skill content exceeds %s characters" % limit)
        directory, path = self.paths(name)
        try:
            os.mkdir(directory)
        except OSError:
            pass
        write_text(path, _skill_text(name, description, body))
        return {
            "name": name,
            "description": description,
            "chars": len(body),
        }

    def list(self, limit=50):
        try:
            names = os.listdir(self.root)
        except OSError:
            return []
        result = []
        for name in names[:limit]:
            try:
                validate_name(name)
            except SkillError:
                continue
            _, path = self.paths(name)
            try:
                text = read_text(path)
            except OSError:
                continue
            metadata, _ = _parse_frontmatter(text)
            result.append({
                "name": name,
                "description": metadata.get("description", ""),
                "chars": len(text),
            })
        return sorted(result, key=lambda item: item["name"])

    def delete(self, name):
        directory, _ = self.paths(name)
        if not self.exists(name):
            raise SkillError("Skill not found: %s" % name)
        self._remove_tree(directory)

    @staticmethod
    def _remove_tree(path):
        try:
            names = os.listdir(path)
        except OSError:
            return
        for name in names:
            child = path + "/" + name
            try:
                os.remove(child)
            except OSError:
                SkillStore._remove_tree(child)
                try:
                    os.rmdir(child)
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except OSError:
            pass
