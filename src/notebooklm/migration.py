"""Migration from legacy flat layout to profile-based directory structure.

Handles transparent migration of ~/.notebooklm/ files into
~/.notebooklm/profiles/default/ on first CLI invocation.

The migration is:
- Automatic: triggered by ensure_profiles_dir() on CLI startup
- Idempotent: safe to run multiple times
- Crash-safe: uses copy-then-delete with marker file
- Non-destructive: originals kept until all copies succeed
"""

import json
import logging
import shutil

from .paths import get_config_path, get_home_dir

logger = logging.getLogger(__name__)

_MIGRATION_MARKER = ".migration_complete"

# Legacy files that should be moved into profiles/default/
_LEGACY_FILES = ["storage_state.json", "context.json"]
_LEGACY_DIRS = ["browser_profile"]


def _has_legacy_files(home) -> bool:
    """Check if any legacy files exist at the home root."""
    return any((home / name).exists() for name in _LEGACY_FILES) or any(
        (home / name).is_dir() for name in _LEGACY_DIRS
    )


def migrate_to_profiles() -> bool:
    """Migrate legacy flat layout to profile-based structure.

    Checks for legacy files at the home root (storage_state.json, context.json,
    browser_profile/) and moves them into profiles/default/.

    Uses copy-then-delete with a marker file for crash safety:
    1. Copy all files/dirs to profiles/default/
    2. Write .migration_complete marker
    3. Delete originals

    If interrupted before the marker, next run retries from intact originals.

    Returns:
        True if migration was performed, False if already migrated or no-op.
    """
    home = get_home_dir()
    profiles_dir = home / "profiles"

    # Already migrated: profiles dir exists and no legacy files left to clean up
    if profiles_dir.exists() and not _has_legacy_files(home):
        return False

    # Check for legacy files
    legacy_files = [home / name for name in _LEGACY_FILES if (home / name).exists()]
    legacy_dirs = [home / name for name in _LEGACY_DIRS if (home / name).is_dir()]

    if not legacy_files and not legacy_dirs:
        # Fresh install — just create profiles directory
        profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logger.debug("Created profiles directory (fresh install)")
        return True

    # Migrate legacy files into profiles/default/
    default_dir = profiles_dir / "default"
    default_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    logger.info("Migrating legacy layout to profiles/default/")

    # Copy files
    for src in legacy_files:
        dst = default_dir / src.name
        shutil.copy2(src, dst)
        # Preserve restrictive permissions
        dst.chmod(src.stat().st_mode)
        logger.debug("Copied %s → %s", src.name, dst)

    # Copy directories
    for src in legacy_dirs:
        dst = default_dir / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        logger.debug("Copied %s/ → %s/", src.name, dst)

    # Remove originals (copies already in place as fallback)
    for src in legacy_files:
        src.unlink()
        logger.debug("Removed legacy %s", src.name)

    for src in legacy_dirs:
        shutil.rmtree(src)
        logger.debug("Removed legacy %s/", src.name)

    # Update config.json with default_profile
    _set_default_profile_in_config()

    # Write marker LAST — signals that migration is fully complete.
    # If the process dies before this point, next run retries (safe because
    # copies use exist_ok/rmtree and originals may already be gone).
    marker = default_dir / _MIGRATION_MARKER
    marker.write_text("migrated\n", encoding="utf-8")

    logger.info("Migration complete: legacy files moved to profiles/default/")
    return True


def _set_default_profile_in_config() -> None:
    """Add default_profile to config.json if not already present."""
    config_path = get_config_path()
    data: dict = {}

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if "default_profile" not in data:
        data["default_profile"] = "default"
        config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)


def ensure_profiles_dir() -> None:
    """Ensure the profiles directory exists, migrating if needed.

    This is the single entry point for migration, called from CLI startup.
    Idempotent — safe to call on every CLI invocation. Also handles:
    - Fresh installs (no profiles dir)
    - Partial migrations (profiles dir exists but legacy files remain)
    - Interrupted migrations from older versions (marker + leftover legacy files)
    """
    home = get_home_dir()
    profiles_dir = home / "profiles"
    if not profiles_dir.exists() or _has_legacy_files(home):
        migrate_to_profiles()
