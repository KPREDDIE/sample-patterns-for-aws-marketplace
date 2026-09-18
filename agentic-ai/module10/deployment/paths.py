"""Keep deployment files in the ignored local directory or outside Git."""
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]


def deployment_path(value):
    path = Path(value).expanduser().resolve()
    root = next((parent for parent in MODULE_ROOT.parents if (parent / ".git").exists()),
                MODULE_ROOT.parent)
    # Resolve the requested path, but not the allowed prefix: a symlink from
    # .local into tracked source must not bypass the boundary.
    local = MODULE_ROOT / ".local"
    if path.is_relative_to(root) and not path.is_relative_to(local):
        raise ValueError("Store deployment files in module10/.local/ or outside the Git repository")
    return path
