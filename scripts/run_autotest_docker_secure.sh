#!/usr/bin/env bash
# Запуск autotest_llm_csv.py в Docker: выбор aiRAG / OpenWebUI pipeline.
#
# Токены и AUTOTEST_OWUI_MODEL подтягиваются из /srv/rag-server/.env (BEARER_TOKEN, AUTOTEST_BEARER_TOKEN и др.).
# Переопределение: export или присвоение AIRAG_BEARER_TOKEN / OWUI_BEARER_TOKEN в шапке ниже.
#
# Использование (с хоста):
#   chmod +x app/scripts/run_autotest_docker_secure.sh
#   ./app/scripts/run_autotest_docker_secure.sh
#   ./app/scripts/run_autotest_docker_secure.sh --limit 1 --timeout-s 180
#
# Неинтерактивно (CI):
#   AUTOTEST_LLM_SOURCE=airag ./app/scripts/run_autotest_docker_secure.sh --limit 1
#   AUTOTEST_LLM_SOURCE=owui ./app/scripts/run_autotest_docker_secure.sh --limit 1
#
# Опционально:
#   AUTOTEST_CONTAINER=my-api
#   AUTOTEST_BASE_URL=http://127.0.0.1:8000
#   AUTOTEST_OWUI_MODEL=rag_pipeline_v3_1_3.rag_pipeline_v3_1_3   # из .env или export
set -euo pipefail

ENV_FILE="/srv/rag-server/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # .env в формате docker-compose (пробелы вокруг =); нормализуем перед source
  # shellcheck disable=SC1090
  source <(sed -E \
    -e '/^[[:space:]]*#/d' \
    -e '/^[[:space:]]*$/d' \
    -e 's/^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*(.*)$/\1=\2/' \
    -e 's/^([^=]+)="([^"]*)"$/\1=\2/' \
    -e "s/^([^=]+)='([^']*)'$/\1=\2/" \
    "$ENV_FILE")
  set +a
  export BEARER_TOKEN AUTOTEST_BEARER_TOKEN AUTOTEST_OWUI_MODEL AUTOTEST_OWUI_BASE_URL
fi

# --- локальная конфигурация (export/присвоение переопределяет .env) ---
: "${AIRAG_BEARER_TOKEN:=}"
: "${OWUI_BEARER_TOKEN:=}"
AIRAG_BEARER_TOKEN="${AIRAG_BEARER_TOKEN:-${BEARER_TOKEN:-${AUTOTEST_BEARER_TOKEN:-}}}"
OWUI_BEARER_TOKEN="${OWUI_BEARER_TOKEN:-${AUTOTEST_BEARER_TOKEN:-${OPENWEBUI_API_KEY:-}}}"
# Id модели/pipeline на OpenWebUI (обязателен для owui); prod: rag_pipeline_v3_1_3.rag_pipeline_v3_1_3
: "${AUTOTEST_OWUI_MODEL:=rag_pipeline_v3_1_3.rag_pipeline_v3_1_3}"
# ---

CONTAINER="${AUTOTEST_CONTAINER:-llm-products}"
SCRIPT_IN_CONTAINER="/app/scripts/autotest_llm_csv.py"

BASE_URL="${AUTOTEST_BASE_URL:-http://localhost:8000}"
OWUI_BASE_URL="${AUTOTEST_OWUI_BASE_URL:"
DB_PATH="${AUTOTEST_DB_PATH:-/app/data/database.db}"
INPUT_CSV="${AUTOTEST_INPUT_CSV:-/app/data/all_tests_query.xlsx.csv}"
OUTPUT_DIR="${AUTOTEST_OUTPUT_DIR:-/app/data/rarequests/tests}"

die() {
  echo "Ошибка: $*" >&2
  exit 1
}

choose_llm_source() {
  if [[ -n "${AUTOTEST_LLM_SOURCE:-}" ]]; then
    case "$AUTOTEST_LLM_SOURCE" in
      airag|owui) echo "$AUTOTEST_LLM_SOURCE"; return ;;
      *) die "AUTOTEST_LLM_SOURCE должен быть airag или owui, получено: $AUTOTEST_LLM_SOURCE" ;;
    esac
  fi

  echo "Выберите источник ответов LLM:" >&2
  echo "  1) aiRAG (FastAPI /ask)" >&2
  echo "  2) OpenWebUI pipeline" >&2
  read -r -p "Ваш выбор [1-2]: " choice
  case "$choice" in
    1) echo "airag" ;;
    2) echo "owui" ;;
    *) die "Неверный выбор: $choice" ;;
  esac
}

LLM_SOURCE="$(choose_llm_source)"

DOCKER_ENV=(
  -e "AUTOTEST_DB_PATH=$DB_PATH"
  -e "AUTOTEST_INPUT_CSV=$INPUT_CSV"
  -e "AUTOTEST_OUTPUT_DIR=$OUTPUT_DIR"
)

case "$LLM_SOURCE" in
  airag)
    if [[ -z "$AIRAG_BEARER_TOKEN" ]]; then
      die "Пустой AIRAG_BEARER_TOKEN. Задайте BEARER_TOKEN в .env или export AIRAG_BEARER_TOKEN (режим aiRAG)."
    fi
    DOCKER_ENV+=(
      -e "AUTOTEST_BEARER_TOKEN=$AIRAG_BEARER_TOKEN"
      -e "AUTOTEST_BASE_URL=$BASE_URL"
    )
    PY_ARGS=(--llm-source airag)
    ;;
  owui)
    if [[ -z "$OWUI_BEARER_TOKEN" ]]; then
      die "Пустой OWUI_BEARER_TOKEN. Задайте AUTOTEST_BEARER_TOKEN или OPENWEBUI_API_KEY в .env (режим OpenWebUI pipeline)."
    fi
    if [[ -z "$AUTOTEST_OWUI_MODEL" ]]; then
      die "Пустой AUTOTEST_OWUI_MODEL. Задайте в .env, шапке скрипта или export AUTOTEST_OWUI_MODEL (режим owui)."
    fi
    DOCKER_ENV+=(
      -e "AUTOTEST_BEARER_TOKEN=$OWUI_BEARER_TOKEN"
      -e "AUTOTEST_OWUI_BASE_URL=$OWUI_BASE_URL"
      -e "AUTOTEST_OWUI_MODEL=$AUTOTEST_OWUI_MODEL"
    )
    PY_ARGS=(--llm-source owui)
    ;;
  *)
    die "Неизвестный источник LLM: $LLM_SOURCE"
    ;;
esac

exec docker exec -it \
  "${DOCKER_ENV[@]}" \
  "$CONTAINER" python "$SCRIPT_IN_CONTAINER" "${PY_ARGS[@]}" "$@"
