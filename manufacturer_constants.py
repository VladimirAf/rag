
from typing import Final

RAWMID_MANUFACTURER_ID: Final[int] = 46
UNMARKED_MANUFACTURER_ID: Final[int] = 93


def normalize_csv_manufacturer_id(raw: str | None) -> int:
    """0 / пусто / NULL в CSV → UNMARKED (93); иначе целое manufacturer_id."""
    if raw is None:
        return UNMARKED_MANUFACTURER_ID
    value = str(raw).strip()
    if not value or value.upper() == "NULL" or value == "0":
        return UNMARKED_MANUFACTURER_ID
    try:
        return int(value)
    except ValueError:
        return UNMARKED_MANUFACTURER_ID
