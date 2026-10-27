"""Local SKILL.md packages with mode/risk filtering and audited calls."""

from .library import (
    MAX_REFERENCE_CHARS,
    MAX_SKILLS,
    Skill,
    SkillError,
    SkillIssue,
    SkillLibrary,
    render_invocation,
)
from .manifest import (
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_REFERENCE_BYTES,
    MAX_SKILL_FILE_BYTES,
    SkillManifest,
    SkillManifestError,
    parse_manifest,
)

__all__ = [
    "MAX_REFERENCE_CHARS",
    "MAX_SKILLS",
    "Skill",
    "SkillError",
    "SkillIssue",
    "SkillLibrary",
    "render_invocation",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_REFERENCE_BYTES",
    "MAX_SKILL_FILE_BYTES",
    "SkillManifest",
    "SkillManifestError",
    "parse_manifest",
]
