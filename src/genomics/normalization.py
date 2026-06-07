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
    
    # Split by period/dot unless preceded by mr or dr
    name_clean = re.split(r'(?<!\bmr)(?<!\bdr)\.', name_clean)[0]
    
    # Split by special genealogy/divider characters
    name_clean = re.split(r'[»«>:<]', name_clean)[0]
    
    # Strip parenthesized, bracketed, or curly-bracketed content
    name_clean = re.sub(r"\s*\([^)]*\)", "", name_clean)
    name_clean = re.sub(r"\s*\[[^\]]*\]", "", name_clean)
    name_clean = re.sub(r"\s*\{[^}]*\}", "", name_clean)
    
    # Strip common breeding/phenotype/type/source suffixes as whole words
    name_clean = re.sub(
        r"\b(bx\d*|auto|f\d*|s\d*|ix|ale|cut|clone|pheno\d*|phenotype\d*|selection|elite|pollen|seeds?|backcross|mostly\s+(indica|sativa|hybrid))\b",
        "",
        name_clean
    )
    
    # Remove all non-alphanumeric characters
    return re.sub(r"[^a-z0-9]", "", name_clean)

