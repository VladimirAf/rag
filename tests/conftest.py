"""
Лёгкое окружение для pytest без полного набора прод-зависимостей LangChain/Chroma.

`app/core.py` импортирует тяжёлые модули на уровне файла (`llm_funcs`, `routes.*`).
Для юнит-тестов нам достаточно заглушек до импорта `core`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sys
import types


def _load_routes_files_sku_hints() -> types.ModuleType:
    """Реальный sku_hints.py без LangChain; для `from routes.files.sku_hints import ...` в core."""
    fq = "routes.files.sku_hints"
    existing = sys.modules.get(fq)
    if isinstance(existing, types.ModuleType) and hasattr(existing, "extract_model_sku_hints"):
        return existing

    path = Path(__file__).resolve().parents[1] / "routes/files/sku_hints.py"
    spec = importlib.util.spec_from_file_location(fq, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load sku_hints from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def pytest_configure():
    """Вызывается pytest до загрузки тестовых модулей."""

    app_dir = str(Path(__file__).resolve().parents[1])

    # Гарантируем импорт пакетов приложения как `core`, `llm_funcs`, ...
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    # --- llm_funcs: без langchain_* ---
    if "llm_funcs" not in sys.modules:
        llm_funcs = types.ModuleType("llm_funcs")

        def _noop(*_args, **_kwargs):
            return None

        llm_funcs.invoke_json = _noop  # type: ignore[attr-defined]
        llm_funcs.invoke = _noop  # type: ignore[attr-defined]
        llm_funcs.check_context_sufficiency = _noop  # type: ignore[attr-defined]
        llm_funcs.judge_files_sufficiency = _noop  # type: ignore[attr-defined]
        llm_funcs.parse_query = _noop  # type: ignore[attr-defined]
        llm_funcs.find_related_products = _noop  # type: ignore[attr-defined]
        llm_funcs.summarize_user_query = lambda q, **_: q  # type: ignore[attr-defined]

        sys.modules["llm_funcs"] = llm_funcs

    # --- routes.*: без импорта chromadb/langchain из crud ---
    if "routes" not in sys.modules:
        routes = types.ModuleType("routes")
        routes.__path__ = []  # type: ignore[attr-defined]  # pretend namespace package
        sys.modules["routes"] = routes

    def _ensure_submodule(parent: str, name: str) -> types.ModuleType:
        fq = f"{parent}.{name}"
        mod = sys.modules.get(fq)
        if isinstance(mod, types.ModuleType):
            return mod
        m = types.ModuleType(fq)
        sys.modules[fq] = m
        parent_mod = sys.modules[parent]
        setattr(parent_mod, name, m)
        return m

    tickets_pkg = _ensure_submodule("routes", "tickets")
    files_pkg = _ensure_submodule("routes", "files")
    files_pkg.__path__ = [str(Path(app_dir) / "routes/files")]  # type: ignore[attr-defined]

    tickets_crud = _ensure_submodule("routes.tickets", "crud")
    files_crud = _ensure_submodule("routes.files", "crud")

    if not hasattr(tickets_crud, "search"):
        tickets_crud.search = lambda *a, **k: None  # type: ignore[attr-defined]
    if not hasattr(files_crud, "search_staged"):
        files_crud.search_staged = lambda *a, **k: None  # type: ignore[attr-defined]

    # Подстраховка: ссылки из пакетов (как при обычном import routes.tickets)
    setattr(tickets_pkg, "crud", tickets_crud)
    setattr(files_pkg, "crud", files_crud)

    sku_hints_mod = _load_routes_files_sku_hints()
    setattr(files_pkg, "sku_hints", sku_hints_mod)
