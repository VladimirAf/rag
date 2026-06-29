# Changelog

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/). Записи дополняются по мере переноса задач в `.cursor/rules/workflow/done/archive/`.

## [2026-06-22]

### Добавлено

- **`query_has_part_intent`**: `skip_best_priority` при «запчаст» или part-стемах (`нож`, `штыр`, …); лог `priority_filter_mode=skip_best_priority`.
- **Backlog epic** FTS recall: `.cursor/rules/workflow/backlog/20260622-products-fts-recall-improvements.md`.
- **Wiki** §13: BM25, OR-FTS, name-prefix boost, word-boost, parallel FTS; §12.4 таблица R0–R5.

### Изменено

- **`_should_keep_accessory_products`**: принимает `query`, вызывает `product_schema_lookup.query_has_accessory_intent`.

## [2026-06-19]

### Добавлено

- **Direct SKU guards epic (§A–B)**: NER guard при exact SKU, `accessories_allowed` для `other_products`; тесты `test_find_products_ner_guard_direct_sku.py`, `test_find_products_priority_accessory_guard.py`.
- **Schema context noise epic**: strict mention filter, score, `PRODUCT_SCHEMA_MAX_HITS`, dual `link_type` для картриджей, skip `find_related_products` и skip category search при `schema_lookup_used`; golden-тесты и `app/scripts/run_find_context_golden.py`.
- **Tiered-cap**: `PRODUCTS_CONTEXT_MAX`, `test_find_products_context_cap.py`.
- **Wiki products-search-pipeline**: §3 шаг 2b (mention-токены, стемы, skip-by-SKU), §8–11, tiered-cap §11.

### Изменено

- **`extract_mention_tokens`**: поддержка кириллицы в `_normalize_token_compact`; fix skip-by-SKU (`if compact and …`).
- **`find_products`**: guards после schema lookup; golden #2 97→7 SKU, #3 100→7 SKU.
- **Tiered-cap `PRODUCTS_CONTEXT_MAX=15`**: `_cap_products_tiered` в `find_products`; golden #4 20→15 SKU.

### Исправлено

- Промах golden #4 (картридж RMC-01): dual `link_type` part+accessory.
- Раздувание контекста при `mention_tokens=[]` (весь schema-список).

## [2026-04-16]

### Добавлено

- **Метрики aiRAG в БД и debug для `/ask`**: колонка `metrics` (TEXT, NULL) в таблице `rarequests` с идемпотентной миграцией; параметр запроса `debug` у `POST /ask`; при `debug=true` в `rarequests.metrics` сохраняется JSON с полями вроде `model`, `duration_ms`, `tokens_input`, `tokens_output`, `tokens_total` (`app/models.py`, `app/main.py`, `app/core.py`).
- **Автотестовый генератор CSV**: скрипт `app/scripts/autotest_airag_csv.py` читает пул из `app/data/all_tests_queries.csv`, вызывает `POST /ask` с `source=autotest_<id>`, собирает ответы и aiRAG-колонки из SQLite и пишет `app/data/rarequests/tests/<ddmmyyyy-HHMM>.csv` (`;`, UTF-8 BOM).

### Изменено

- **Список тестов `/tests`**: под блоками rag-tool и pipeline добавлена сводная статистика по колонке `оценка aiRAG` (включая учёт `нет оценки` и сообщение «этот тест никто не оценивал» при отсутствии реальных оценок); для CSV без aiRAG-колонок блок скрывается (`rarequests/app.py`, `rarequests/templates/testselect.html`).
- **Просмотр теста `/tests/<file>`**: при наличии всех aiRAG-колонок в строке — кнопка **aiRAG** и полноэкранная модалка (ответ и разбор-коммент); модалка «Статистика» разбита на три группы — rag-tool, pipeline, aiRAG — с полями состава контекста и статистики aiRAG; единое нейтральное отображение для пустого значения и «нет оценки» (`rarequests/templates/testrarequest.html`).

## [2026-04-18]

### Добавлено

- **Intent routing: конфиг маршрутов и порог уверенности**: `app/config/intent_routes.json` (известные интенты, маршруты и настройки порядка `files`), загрузчик с Pydantic-валидацией `app/intent_routes_loader.py`; в конфиг добавлены `INTENT_ROUTES_JSON_PATH` и `INTENT_CONFIDENCE_THRESHOLD` (env, дефолт 0.65); при невалидном JSON сервис не стартует (`app/config.py`, `app/main.py`).
- **LLM-классификатор интента (structured output)**: модель `IntentClassifierOutput` и константа `UNDEFINED_INTENT_LABEL`; функция `classify_intent()` с нормализацией/разрешением label, чтением списка интентов из `intent_routes.json`, и безопасным фоллбеком в `undefined` при ошибках/парсинге (`app/models.py`, `app/intent_classifier.py`).
- **Роутер интента и fallback на базовый пайплайн**: модуль `app/intent_router.py` с `ResolvedIntentRoute` и `resolve_intent_route()` (id маршрута, порядок стадий, override порядка `files`); неизвестный label/`undefined` → fallback на прежний порядок (products → tickets → files) с дефолтным staged‑порядком; добавлены юнит‑тесты (`app/tests/test_intent_router.py`).
- **Наблюдаемость для intent routing**: безопасный append JSONL в `app/data/logs/undefined_intents.log` для `undefined`/низкой уверенности без влияния на успешность ответа; нормализация меток маршрута; диагностические логи `find_context routing` (intent/confidence/маршрут/порядок стадий) и `find_context early_exit` (стадия/индекс); добавлены тесты и обновлены моки (`app/intent_observability.py`, `app/tests/test_intent_observability.py`, `app/tests/test_find_context_intent_integration.py`).
- **Override порядка staged‑поиска в `files`**: в `search_staged` добавлен опциональный параметр `data_type_order` и нормализация override; без параметра поведение и дефолтный порядок стадий сохранены (`app/routes/files/crud.py`).
- **Операционная приёмка intent routing + безопасный автопрогон**: зафиксирован фактический entry point `/ask` → `core.ask` → `core.find_context`; добавлен скрипт безопасного запуска автопрогона в Docker с вводом токена через `read -s`; добавлен симлинк `app/data/all_tests_query.xlsx.csv` → `all_tests_queries.csv` под имя из ТЗ (`app/scripts/run_autotest_docker_secure.sh`, `app/data/all_tests_query.xlsx.csv`).

### Изменено

- **`find_context`: выполнение стадий по маршруту интента**: добавлена ранняя классификация и выбор маршрута; стадии retrieval выполняются в порядке маршрута с сохранением early exit; для `files` прокинут `data_type_order` из `intent_routes.json`; добавлены тесты на порядок стадий и сохранение fallback‑поведения (`app/core.py`, `app/tests/test_find_context_intent_integration.py`).

## [2026-05-13]

### Добавлено

- **Операционная приёмка intent routing (результаты)**: зафиксированы фактические артефакты приёмки по ручным сценариям ТЗ; для запроса «Как оплатить заказ на сайте?» в `app/data/logs/app.log` подтверждены строки `find_context routing intent=service_policy route_applied=service_policy_route stages=tickets,files,products` и `find_context early_exit ... stage=tickets`; составлена таблица соответствий ручных сценариев ожидаемым маршрутам и early exit; зафиксированы инструкции прогона второго этапа (CSV‑пакет) через `app/scripts/run_autotest_docker_secure.sh` с безопасным вводом токена (`.cursor/rules/workflow/done/archive/20260418-intent-acceptance-tests.md`).

## [2026-05-26]

### Добавлено

- **KB-ингест: `filename` в metadata чанков**: в `add_file` для каждого чанка задаётся `doc.metadata["filename"]` из имени загрузки; предупреждения контракта tm через `warn_kb_ingest_if_needed`; расширено описание `POST /files/add` для `source=tm_knowledge_base` (`app/routes/files/crud.py`, `app/routes/files/kb_ingest.py`, `app/routes/files/router.py`, `app/tests/test_add_file_kb_metadata.py`).
- **Модуль сущностей KB-files retrieval**: `app/routes/files/retrieval_entities.py` — нормализация unicode dash, token overlap с `kb_product_titles`, проверка SKU в каноне, `build_files_embedding_query`, `is_kb_chunk_metadata`; юнит-тесты `app/tests/test_files_retrieval_entities.py`.
- **Композитный реранк KB-чанков**: `app/routes/files/kb_chunk_rerank.py` — скор по SKU / overlap / категориям / canonical SKU, режимы broad/narrow, tie-break `kb_category_priority`, debug top-3; тесты `app/tests/test_kb_chunk_rerank.py`.

### Изменено

- **`find_context`: один `parse_query` на запрос**: `parse_query` + `_merge_regex_skus_into_parse_results` вызываются один раз до стадий retrieval; результат переиспользуется в `find_products` и `search_staged`; `find_products` принимает опциональный `parse_results` (`app/core.py`, `app/tests/test_find_context_intent_integration.py`).
- **`search_staged`: обогащение embedding и KB-реранк на стадии `files`**: опциональный `parse_results` прокидывается из `find_context`; для KB-чанков (`source=tm_knowledge_base` или префикс `knowledge_base` в `filename`) — расширенный `embedding_query` и `rerank_stage_docs` в `_retrieve_stage_docs_vector`; для не-KB файлов поведение без изменений (`app/routes/files/crud.py`).

## [2026-05-29]

### Добавлено

- **Единый CSV-автотест с `--llm-source`**: общий модуль `app/scripts/test_csv_common.py` (заголовки, чтение/запись CSV, helpers SQLite); раннер `app/scripts/autotest_llm_csv.py` с `--llm-source airag|owui` и `--limit`; `autotest_airag_csv.py` — thin wrapper с default `airag` (регрессия: `source=autotest_<id>`, колонки aiRAG).
- **Автотест OpenWebUI pipeline**: `app/scripts/autotest_owui_client.py` — `POST .../api/v1/chat/completions`, `metadata.chat_id=<run_id>`, парсинг ответа/stats, retry SQLite; ветка `owui` в `autotest_llm_csv.py` с CLI `--owui-base-url`, `--owui-model` (обязателен); колонки pipeline в CSV, aiRAG/rag-tool пусты.
- **Документация OWUI-автотеста**: `docs/openwebui/README.md` — HTTP-контракт, env (`AUTOTEST_OWUI_BASE_URL`, `AUTOTEST_OWUI_MODEL`, `OWUI_BEARER_TOKEN`), curl/docker (режим 2), связь `metadata.chat_id` → `source=openwebui_pipeline_<id>`; prod model: `rag_pipeline_v3.rag_pipeline_v3`.
- **UI тестов: блокировка неактивных источников**: `detect_test_sources()` в `rarequests/app.py`; на `/rarequests/tests/<file>` кнопки rag-tool / pipeline / aiRAG отключаются (`test-source-disabled`, `aria-disabled`), если в CSV нет данных для источника (`rarequests/templates/testrarequest.html`).

### Изменено

- **`run_autotest_docker_secure.sh`**: интерактивное меню `1) aiRAG` / `2) OpenWebUI pipeline`; константы `AIRAG_BEARER_TOKEN` / `OWUI_BEARER_TOKEN` в шапке; CI-режим `AUTOTEST_LLM_SOURCE=airag|owui`; запуск `autotest_llm_csv.py` с пробросом `"$@"`.

### Примечание (приёмка)

- При live-прогоне OWUI pipeline записи в `rarequests` могут иметь `source=openwebui_pipeline__userid_<uuid>` вместо ожидаемого `openwebui_pipeline_<run_id>`; CSV заполняется через fallback (`.cursor/rules/workflow/done/archive/20260529-bug-owui-pipeline-source-run-id.md`).

## [2026-06-04]

### Добавлено

- **Topical fallback для `seocrm_article` при `malfunction_howto`**: модуль `app/routes/files/topical_fallback.py` — `topical_match_strength`, `topical_select_candidate_indexes`; при пустом `selected_candidate_indexes` после judge на стадии `seocrm_article` детерминированный выбор чанка по лексическому overlap запроса с title/excerpt (порог `TOPICAL_FALLBACK_MIN`); тесты `app/tests/test_seocrm_topical_fallback.py`.

### Изменено

- **`search_staged`**: параметр `intent_label`; topical fallback включается на `seocrm_article` при интенте `malfunction_howto` (`app/routes/files/crud.py`).
- **`find_context`**: проброс `intent_label` из классификатора в `search_staged` (`app/core.py`).
- **Промпт `judge_files_sufficiency`**: уточнение для `seocrm_article` + запросы про уход/протечки/«что проверить» (`app/llm_funcs.py`).
- **`docs/terms.md`**: раздел topical fallback (отличие от hard-select и enough-gates).

## [2026-06-08]

### Добавлено

- **Поля производителя в модели и API продуктов**: `MANUFACTURER_ID=46`; в `Product` и `PRODUCT_COLS` — `manufacturer_id`, `product_id`; `POST /products/create` принимает `manufacturer_id` (default 46); `POST /products/edit` может менять `manufacturer_id` (`app/models.py`, `app/routes/products/crud.py`, `app/routes/products/router.py`).

## [2026-06-16]

### Добавлено

- **KB ingest: склейка микрочанков транскриптов**: для файлов БЗ с `metadata.ai_file_type="transcript"` добавлено объединение коротких `Document`-элементов после `UnstructuredLoader` в абзацы (целевой размер ≥300 символов) перед нарезкой `RecursiveCharacterTextSplitter`; хелперы `is_transcript_file` и `merge_short_documents` вынесены в `app/routes/files/kb_ingest.py`, интеграция выполнена в `add_file` (`app/routes/files/crud.py`).
- **Юнит-тесты merge транскриптов**: добавлены тесты `is_transcript_file`/`merge_short_documents` и проверка интеграции в `add_file` через моки без Chroma/LLM (`app/tests/test_kb_transcript_merge.py`).

### Примечание (приёмка)

- После реиндекса `knowledge_base_kb6_*` запрос `POST /find_context` про эксплуатацию климатического комплекса возвращает в контексте KB-файл `knowledge_base_kb6_file147_v1_20231129_eE8IK_BDbqM_.ru.txt`; для этого файла доля чанков ≥300 символов составила 97%+ (вместо ~40-символьных микрочанков).

## [2026-06-18]

### Исправлено

- **`extract_mention_tokens`: кириллица в schema lookup (R1)**: добавлена `_normalize_token_compact` (латиница + кириллица `а-яё` + цифры); skip-by-SKU срабатывает только при непустом `compact` — кириллические слова golden set («подшипник», «воронка», «лопатка», «крышка», «картридж») больше не отбрасываются; unit-тесты в `app/tests/test_product_schema_lookup.py` (`app/product_schema_lookup.py`).
- **Schema lookup: strict mention + score + cap (R2, R6, R7)**: `_filter_by_mention` возвращает top-K по score при 0 mention matches (default K=3), fallback по стемам `_STEMS_FROM_QUERY` при пустых tokens; `PRODUCT_SCHEMA_MAX_HITS=5` ограничивает выдачу; лог `returned`/`dropped_noise`; golden #1/#3/#5 и unit-тесты в `app/tests/test_product_schema_lookup.py` (`app/product_schema_lookup.py`, `app/config.py`).
