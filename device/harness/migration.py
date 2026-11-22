import os

from .config import join_path, read_json, read_text, remove_file
from .skills import SkillError, validate_name


def migrate_legacy_skills(root, skills):
    """Move old /skills/<name>.py records into Markdown Skills.

    Custom executable tools are no longer supported, so every legacy program
    is preserved as a Markdown instruction example instead of being turned
    into a runnable tool.
    """
    old_root = join_path(root, "skills")
    try:
        names = os.listdir(old_root)
    except OSError:
        return 0
    migrated = 0
    for filename in names:
        if not filename.endswith(".py") or filename.endswith(".prev.py"):
            continue
        name = filename[:-3]
        try:
            validate_name(name)
        except SkillError:
            continue
        source_path = old_root + "/" + filename
        metadata_path = old_root + "/" + name + ".json"
        try:
            source = read_text(source_path)
        except OSError:
            continue
        metadata = read_json(metadata_path, {})
        try:
            description = metadata.get("task") or "Migrated program"
            body = "# Migrated program\n\n```python\n%s\n```\n" % source
            skills.save(name, description, body)
            remove_file(source_path)
            remove_file(old_root + "/" + name + ".prev.py")
            remove_file(metadata_path)
            migrated += 1
        except (OSError, SkillError):
            continue
    return migrated
