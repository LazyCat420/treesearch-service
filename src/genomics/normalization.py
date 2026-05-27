import re

def normalize_strain_name(name: str) -> str:
    """Normalize strain name to lowercase alphanumeric-only string."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower().strip())

def normalize_for_grouping(name: str) -> str:
    if not name:
        return ""
    # Replace underscores with spaces
    name_clean = name.replace("_", " ")
    # Lowercase
    name_clean = name_clean.lower()
    # Strip parenthesized content: e.g. "headband (unknown or legendary)" -> "headband "
    name_clean = re.sub(r"\s*\([^)]*\)", "", name_clean)
    # Strip common breeding/phenotype/type suffixes as whole words
    # Added 'ale' to consolidate Romulan Ale -> Romulan
    name_clean = re.sub(r"\b(bx\d*|auto|f\d*|s\d*|ix|ale)\b", "", name_clean)
    # Remove all non-alphanumeric characters
    return re.sub(r"[^a-z0-9]", "", name_clean)

