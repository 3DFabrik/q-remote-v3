"""Configuration loader for Q-Remote V3.

Loads config.yaml as defaults, then overlays config.local.yaml if present.
config.local.yaml is gitignored and contains production-specific overrides.
"""

import os
from pathlib import Path
from typing import Any

import yaml


# Config file paths (relative to project root)
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_CONFIG = _PROJECT_ROOT / "config.yaml"
_LOCAL_CONFIG = _PROJECT_ROOT / "config.local.yaml"

# Cached config singleton
_config: dict[str, Any] | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(force_reload: bool = False) -> dict[str, Any]:
    """Load configuration from YAML files.
    
    Loads config.yaml first, then overlays config.local.yaml if it exists.
    Results are cached unless force_reload=True.
    """
    global _config
    
    if _config is not None and not force_reload:
        return _config
    
    # Load defaults
    with open(_DEFAULT_CONFIG, "r") as f:
        config = yaml.safe_load(f)
    
    # Overlay local overrides if they exist
    if _LOCAL_CONFIG.exists():
        with open(_LOCAL_CONFIG, "r") as f:
            local = yaml.safe_load(f)
        if local:
            config = _deep_merge(config, local)
    
    # Allow environment variable overrides (QREMOTE_SECTION_KEY=value)
    config = _apply_env_overrides(config)
    
    _config = config
    return _config


def _apply_env_overrides(config: dict) -> dict:
    """Apply environment variable overrides.
    
    Format: QREMOTE_<SECTION>__<KEY>=value
    Example: QREMOTE_SERVER__PORT=9090
    """
    prefix = "QREMOTE_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        
        path = key[len(prefix):].lower().split("__")
        target = config
        for part in path[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        
        # Try to parse as int/float/bool
        target[path[-1]] = _parse_env_value(value)
    
    return config


def _parse_env_value(value: str) -> Any:
    """Parse environment variable value to appropriate Python type."""
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def get(key_path: str, default: Any = None) -> Any:
    """Get a config value by dot-separated path.
    
    Examples:
        get("radio.device")       -> "/dev/ttyACM0"
        get("audio.chunk_ms")     -> 80
        get("nonexistent", 42)    -> 42
    """
    config = load_config()
    keys = key_path.split(".")
    result = config
    for key in keys:
        if isinstance(result, dict) and key in result:
            result = result[key]
        else:
            return default
    return result
