import re

def normalize_strain_name(name: str) -> str:
    """Normalize strain name to lowercase alphanumeric-only string."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())
