# 08. Контракт MCP-инструментов, ошибок и сессий

Раздел единой базы знаний (мастер-индекс — `simintech-code/docs/knowledge/README.md`).
Описывает обязательства сервера перед клиентом-агентом и границы, которые он
не обещает. Исполняемая версия контракта — `tests/unit/test_surface.py` и
другие тесты `tests/unit/`; здесь — свод и «почему так».

## 1. Отказ — исключение, а не текст

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-failure-is-exception` | Любой отказ инструмента превращается в `ToolError` (`isError` у клиента); исключение или строка `"ERROR: …"` — обе формы | code-confirmed | `simintech_mcp/runtime.py:163-183`; `test_runtime.py` |
| `mcp-error-prefix-contract` | Отказ строкой обязан начинаться с `ERROR:`; текст вида «Error: …» уходит клиенту **успехом** и пишется в журнал как `contract-drift` | code-confirmed | `runtime.py:49-50, 179-198` |
| `mcp-contract-drift-logging` | Сам детектор `contract-drift` не покрыт ни одним тестом (находка аудита) — контракт держится только кодом | code-confirmed, оговорка: тестов нет | `runtime.py:186-198` |

## 2. COM-поток и время

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-single-com-thread` | Все COM-вызовы идут через один выделенный поток (`max_workers=1`) — это условие работоспособности: чужой поток даёт `CO_E_OBJNOTCONNECTED` | code-confirmed | `runtime.py:36-47`; `test_runtime.py:17` |
| `mcp-com-timeout-not-interrupting` | `COM_CALL_TIMEOUT` (120 с) **не прерывает** COM-вызов: по таймауту клиент получает отказ, а работа может ещё идти; штатный долгий `run` восстанавливается `disconnect` + пересозданием проекта | live-verified | замер 17.09.2026; `runtime.py:337-350` |
| `mcp-stuck-call-instant-refusal` | Зависший вызов запоминается: следующие вызовы отказывают **мгновенно** с диагнозом живости mmain (вне COM); `disconnect` (rescue) не отсекается — встаёт в очередь | live-verified | репорт fdd002 08.10.2026; `runtime.py:75-160, 327-335` |
| `mcp-contour-reads-shift-model-time` | «Читающие» контурные инструменты (`export_model_text`, `check_model_layout`, `audit_routing`, `save_screenshot`, `get_page_script` — нет) **перезапускают расчёт и сдвигают модельное время** как `step`; названо в докстрингах | code-confirmed | `tools/page_script.py:193-226`; докстринги инструментов |

Следствие для агента: перед контурным чтением результаты текущего расчёта
надо либо забрать (`read_output_file`), либо принять сдвиг времени.

## 3. Сессия: владение, смена проекта, пакет

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-ownership-gate-fail-closed` | Подключение принимается только к своему процессу (OWNED); EXTERNAL/UNKNOWN — отказ; чужой экземпляр SimInTech не тронут | code-confirmed | `session.py:211-254`; `test_session.py:535,561` |
| `mcp-project-change-only-via-set-project` | Проект меняется только `session.set_project`; вместе с ним сбрасываются линии сессии, курсоры фитов и счёт несохранённого | code-confirmed | `session.py:286-313` |
| `mcp-pack-member-protection` | Участник открытого пакета не закрывается (`CloseProject` исключил бы его из состава); «неизвестно» трактуется как «нельзя исключить» | live-verified | замер 01.10.2026 (состав 2 → 1); `session.py:430-517` |
| `mcp-session-scene-degradation` | После `save_project` возможна деградация сцены mmain (выгрузка «успешна» без файла; далее AV) — правило: сохранили → `reload_project`/`open_project`, потом page-вызовы | live-verified | живые случаи 06.10.2026; `tools/project.py:714-722` |
| `mcp-save-atomic-temp-file` | `save_project` пишет через `<имя>.tmp.<ext>` + `os.replace`; нулевая длина: отказ, не успех; расширение = формат | live-verified | репорт 08.10.2026; замеры 13.10.2026; `project.py::_verify_saved, save_project` |
| `mcp-save-visible-window` | `save_project` всегда вызывает `FormShow`; после записи проверяет признак видимости (`<visible>`1`</visible>` в `.xprt`, байт 8 zlib-заголовка `.prt`) и при нуле отказывает, не заменяя целевой файл. Параметра `show_form` больше нет | code-confirmed | `project.py::_visible_flag, _require_visible, save_project`; `test_project_tools.py` |
| `mcp-solver-dll-process-cache` | Внешний модуль решателя загружается в процесс один раз; `reload_project` перечитывает проект, но не гарантирует перечитывание DLL. Сравнимы прогоны внутри одного процесса; для смены DLL завершите процесс (Stop-Process) и переподключитесь | live-verified | живой A/B-прогон 31.10.2026; `tools/project.py::reload_project` |

## 4. Контракт правок (issue #18)

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-mutation-note` | Успешный ответ мутирующего инструмента несёт хвост «Изменения внесены в: … Не сохранено: …»; хвост ставит обвязка по метке `mutates_project=True`, а не инструмент | code-confirmed | `runtime.py:200-218`; `session.py:388-406`; `test_surface.py:74-96` |
| `mcp-mutating-set-is-contract` | Список мутирующих — контракт поверхности: лишняя метка — ложь, пропавшая — слепой ответ | code-confirmed | `test_surface.py::test_mutating_tools_are_marked` |

## 5. Песочница и файлы

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-sandbox-limit-before-read` | Чтение — только из каталога результатов (`SIMINTECH_OUTPUT_DIR`, иначе `<tmp>/simintech-output`); предел объёма применяется **до** чтения; симлинк-подмена каталога отвергается | code-confirmed | `sandbox.py:37-120`; `test_sandbox.py` |
| `mcp-sandbox-env-fail-closed` | `SIMINTECH_OUTPUT_DIR`, заданный в несуществующий каталог, — отказ чтения (fail closed), а не «возьмём другой» | code-confirmed | `sandbox.py:65-84` |

## 6. Поверхность: что обязано быть правдой у каждого инструмента

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-every-tool-wrapped` | Каждый инструмент обёрнут `com_threaded` или `plain_tool` — иначе отказ уходит успехом и вызова нет в журнале | code-confirmed | `test_surface.py::test_all_tools_have_failure_contract` |
| `mcp-plain-tools-exactly-ten` | `plain_tool`: ровно 10 инструментов без COM (список закреплён равенством) | code-confirmed | `test_surface.py:202-218` |
| `mcp-tool-annotations-contract` | У каждого инструмента — `title` и `readOnlyHint`; `destructiveHint: True` — ровно семь; `save_screenshot` — `readOnlyHint: False` без мутирующей метки | code-confirmed | PR #160: `test_surface.py::test_tool_annotations_contract` |
| `mcp-readme-counts-contract` | Число инструментов в README (69) и перечень имён сверяются с `tools/list` тестами | code-confirmed | `test_surface.py:129-163` |
| `mcp-contour-core-single-entry` | Мост вызывается только через `page_script.run_contour` (исключение — `check_model.py`, отчётным протоколом) — архитектурный тест | code-confirmed | `test_dependency_contract.py:166` |

## 7. Model IR

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-model-ref-snapshot` | `model_ref`: opaque снимок в памяти (не более двух, LRU); вытесненная ссылка и устаревшая различаются, после правки проекта нужен новый `load_model` | code-confirmed | `model_store.py`; `test_model_store.py`; `test_model_ir_tools.py` |
| `mcp-model-ir-no-xml` | `load_model` не возвращает XML, скрипты и полные значения свойств: только `model_ref`, хеши, счётчики, покрытие и первые расхождения | code-confirmed | `tools/model_ir.py`; `test_model_ir_tools.py` |
| `mcp-model-ir-sources` | `.xprt` текущего проекта снимается SaveProjectXML без расчёта; `model_text` и `topology` идут контуром и сдвигают модельное время; файл в песочнице выбирает источник по расширению | code-confirmed | `tools/model_ir.py`; `test_model_ir_tools.py` |
| `mcp-model-ir-query-caps` | `query_model` отдаёт не больше 200 `items` и `unknown` с `truncated`; `evidence` хранит `items_total`/`unknown_total`; `inspect_node` ограничивает вложенные `ports`/`wires` и несёт `*_total`; после контрактов объединённый список `check_connections` режется тем же пределом | code-confirmed | `tools/model_ir.py`; `test_model_ir_limits.py` |
| `mcp-model-ir-cycles` | `find_cycles` на ациклической цепочке пуст, настоящий цикл и петля провода видны, цепочка 1200 объектов не падает `RecursionError` | code-confirmed | библиотека v0.18.1; `test_model_ir_limits.py` |
| `mcp-format-model-mutates` | `format_model` помечен `mutates_project=True`: он двигает блоки и линии, поэтому ответ называет проект и повышает поколение снимков IR | code-confirmed | `tools/workflows.py`; `test_surface.py` |
| `mcp-influence-query` | `query_model(query="influence")` по `target` отдаёт цепочки с `hops`, условиями, состоянием и `evidence`; `contract` только для утверждённого контракта или измеренного отношения, `known_absences` отдельным списком, ответ ограничен 200 | code-confirmed | `tools/model_ir.py`; `test_model_ir_tools.py`; `simintech-api v0.19.0` |
| `mcp-semantic-coverage` | `load_model` отдаёт компактную `semantics` (счётчики, причины неизвестности, `pilot_scope`); `query_model(query="semantic_coverage")` — полный словарь с `by_class`; `get_model_manifest` добавляет секцию `semantics`, если она помещается в бюджет | code-confirmed | `tools/model_ir.py`; `test_model_ir_tools.py`; `test_model_ir_limits.py` |
| `mcp-contract-checks` | `query_model(check_connections)` добавляет контракты: `fixed`/`nport`/`weights` дают `evaluated`; `portnames` остаётся `not_evaluated` с причиной `portnames_instance_defined`, потому что порты задаёт PortNames экземпляра; субмодель с внутренней страницей сверяется с порт-блоками по именам (порт без блока, лишний блок и общий блок для двух портов дают отказ), внешняя библиотечная субмодель остаётся `not_evaluated` с причиной `external_submodel`; неизмеренный контракт: `not_evaluated` с классом и причиной; `validate_invariants(contract_ports_match)` заменяет `not_evaluated` результатом реестра | code-confirmed | `contracts.py`; `tools/model_ir.py`; `test_contracts_tools.py` |
| `mcp-contract-resource` | `simintech://blocks/contracts` отдаёт покрытие и образец unknown, `.../<класс>`: один контракт; полный дамп 2 МБ не выводится | code-confirmed | `resources.py`; `test_contracts_tools.py` |
| `mcp-submodel-viewer` | `inspect_submodel` отдаёт содержимое субмодели: внутреннюю из снимка IR без COM; внешнюю библиотечную находит в `<корень>/bin/include_mvtu` (`SIMINTECH_PATH` или путь по умолчанию), открывает файл поставки отдельным проектом, снимает `SaveProjectXML` и закрывает его, не меняя текущий проект сессии; `.xprt` читается напрямую, `.prt` только средой (бинарный zlib-поток) | code-confirmed | `tools/submodel.py`; `test_submodel_tools.py` |
| `mcp-build-and-verify` | `build_and_verify` компилирует SystemSpec до COM, собирает ModelSpec и connect, читает фактический `.xprt` и возвращает `passed`/`partial`/`failed`; `partial` не маскируется; portnames без подсказок даёт `port_unknown` и `partial`; `max` и id инвариантов проверяются до сборки; `contract_ports_match` подкрепляется реестром, portnames в нём `not_evaluated`; счётчики полные при усечённом списке, усечение названо; `model_ref` не возвращается, потому что сборка помечает проект изменённым | code-confirmed | `tools/system_spec.py`; `contracts.py`; `test_build_and_verify.py`; `test_contracts_tools.py` |
| `mcp-verify-report-truncated` | `build_and_verify` берёт счётчики из полного `VerificationReport.counts`, а не из усечённого `items`; `max_items` зажимается 1..200; при `report.truncated` отчёт называет усечение | code-confirmed | `tools/system_spec.py`; `test_build_and_verify.py` |

## 8. Чего контракт не обещает

- Частичная правка не откатывается: отказ в середине пачки оставляет
  сделанное (следующая сверка его назовёт) — инструменты проверяют параметры
  **до** COM, но отказ среды в середине откатить нечем.
- Контурные инструменты сдвигают модельное время и перезапускают расчёт —
  это побочный эффект чтения, а не ошибка.
- Ошибка компиляции скрипта невидима через COM: текст — только в окне
  сообщений SimInTech (см. раздел 06 базы, `not-compiled`).
- Песочница ограничивает **чтение**; запись задаёт сам клиент (например,
  `filename` блока «В файл») — читаемость потом проверит `read_output_file`.
- Ресурсы (`simintech://…`) не бросают исключений: причину отказа они
  возвращают текстом (в отличие от одноимённых инструментов).
- Усечение ответа не означает пустоту: `truncated`, `items_total`,
  `violations_total` и `counts` называют полные числа.

## 9. Машинный хвост timing

`run` и `simulate` оканчивают ответ строкой
`timing: {"t_start":<unix_ms>,"t_end":<unix_ms>,"elapsed_ms":<ms>}`.
`t_start` и `t_end` - unix ms сервера, `elapsed_ms` - монотонная длительность
handler; паузы клиента между вызовами не входят. У `simulate` ровно одна
такая строка: вложенный `run` свой хвост не оставляет. Отказ (`isError`)
тоже несёт timing-строку последней.

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-run-simulate-timing` | `run` и `simulate` дают абсолютные `t_start`/`t_end` и монотонный `elapsed_ms`; у `simulate` один хвост, у отказа тоже есть строка | code-confirmed | `runtime.py`; `tools/simulation.py`; `tools/workflows.py`; `test_simulation_tools.py`; `test_workflows.py` |

## 10. Диагностика шага внешнего решателя

`simulate` и `step` принимают необязательный `diagnostics_path`: журнал
JSON Lines схемы `solver-step-diagnostics/1` в каталоге результатов. MCP
читает хвост, проверяет `pid` (процесс mmain сессии) и свежесть по mtime,
берёт последний исход `accepted`/`rejected`; записи `call`/`entry` -
вход в `im_run`, не исход. При сомнении отдаёт `WARNING` и не показывает
значения. Успешная проверка добавляет строку `solver: {...}` перед
`timing:` (у `step` timing нет).

| id | Факт | Статус | Источник |
|---|---|---|---|
| `mcp-solver-step-diagnostics` | `simulate` и `step` читают хвост журнала только по `diagnostics_path`; для исхода обязательны `schema`, `pid`, `t`, `h`, `status`, `Nbad`, `Ngood`, у отказа - `reason` или `code`; `call`/`entry` - якорь входа без обязательных счётчиков; чужой PID, старый mtime, битый JSON, только входы без исхода и отказ без причины дают `WARNING` без значений | code-confirmed | `solver_diagnostics.py`; `tools/simulation.py`; `tools/workflows.py`; `test_solver_diagnostics.py`; `test_simulation_tools.py`; `test_workflows.py` |
