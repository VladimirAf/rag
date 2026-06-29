"""
Обратная совместимость: делегирует в autotest_llm_csv с --llm-source airag.

Предпочтительная точка входа: python autotest_llm_csv.py --llm-source airag ...
"""

from __future__ import annotations

import sys
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from autotest_llm_csv import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(default_llm_source="airag"))
