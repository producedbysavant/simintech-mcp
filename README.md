# simintech-mcp

MCP-сервер (FastMCP) для управления средой динамического моделирования
**SimInTech** из ИИ-агента: создать проект, разместить блоки, соединить,
запустить расчёт, прочитать и записать сигналы.

Сервер — тонкая обёртка над библиотекой
[`simintech-api`](https://github.com/producedbysavant/simintech-code),
которая делает всю работу через внешний COM API (`IMVTU_Server`, сервер
`mmain.exe`). Здесь остаётся только MCP-слой.

## Ограничения

- **Только Windows** — COM API SimInTech доступен лишь там. Требуется
  зарегистрированный COM-объект: `C:\SimInTech64\bin\mmain.exe /regserver`.
- **Сигналы читаются только там, где есть база сигналов.** Обмен идёт через
  список сигналов проекта и подключённую БД. У проекта без базы читать нечего:
  `list_signals` вернёт имена блоков с пометкой «не читается».

## Установка

```bash
pip install -e ".[test]"
```

Зависимость `simintech-api` берётся из
[`simintech-code`](https://github.com/producedbysavant/simintech-code) по тегу.
Для одновременной правки библиотеки и сервера замените её на path-зависимость:

```toml
simintech-api = { path = "../simintech-code", editable = true }
```

## Запуск

```bash
simintech-mcp                  # stdio
python -m simintech_mcp.server
```

Подключение к Claude Code:

```bash
claude mcp add simintech -- simintech-mcp
# или вручную: { "mcpServers": { "simintech": { "command": "simintech-mcp" } } }
```

## Инструменты

| Группа | Инструменты |
|---|---|
| Подключение | `status`, `disconnect` |
| Проекты | `create_project`, `open_project`, `save_project`, `close_project` |
| Блоки и связи | `add_block`, `connect`, `list_blocks` |
| Параметры | `get_block_params`, `set_block_param` |
| Расчёт | `run`, `step`, `stop`, `get_time` |
| Сигналы | `list_signals`, `get_signal`, `set_signal` |
| Layout | `layout_place` |
| Справка | `help_text` |

Ресурсы: `simintech://status`, `simintech://project/blocks`.
Промпты: `create_pid_model`, `create_rc_chain`.

## Важное про параметры блоков

Имена параметров короткие и неочевидные: у «Константы» — `a` (не `y0`),
у «Сумматора» — `a` (число входов задаётся длиной массива, параметра `xn` нет).

`SetBlockProp` **не отвергает неизвестное имя**: запись в несуществующий
параметр проходит без ошибки и ни на что не влияет. Имена берутся из каталога,
сгенерированного из реального SimInTech, — проверяйте их через
`get_block_params`.

## Тестирование

```bash
python3.11 -m pytest tests/unit -q     # без COM, работает и на Linux
```

Интеграционные тесты живут в `simintech-code` — они проверяют библиотеку и COM.

## Связанные репозитории

- [`simintech-code`](https://github.com/producedbysavant/simintech-code) —
  библиотека `simintech-api`, примеры, справочник встроенного языка SimInTech.
- [`simintech-skill`](https://github.com/producedbysavant/simintech-skill) —
  доменные знания (скиллы) для агента.

## Документация

- [Официальная справка SimInTech](https://help.simintech.ru/) — первоисточник.
  Ключевые разделы: [API](https://help.simintech.ru/27_SimInTech_api/DIR_api.html),
  [командная строка](https://help.simintech.ru/27_SimInTech_api/DIR_komandnaya_stroka.html).
- `docs/roadmap-agentic-ecosystem.md` — состояние экосистемы и планы.
