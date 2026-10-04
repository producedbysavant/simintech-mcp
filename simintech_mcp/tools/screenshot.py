"""Снимок схемы: `savescreenshot` в контуре — изображение текущего вида проекта.

Штатная функция встроенного языка сохраняет текущее изображение экрана
проекта в файл: тип 1 — BMP, 2 — PNG, 3 — SVG (справка поставки, webhelp:
`…/graficheskie/savescreenshot.html`). Живые замеры (поставка 2.26.6.23):
тип 1 — BMP 32bpp; тип 2 — настоящий PNG (1026×580, 86 КБ против 4,4 МБ
у BMP); тип 3 — SVG (UTF-8 XML, вектор). Расширение имени файла формат **не
выбирает** (тип 1 пишет BMP и при имени `.png` — проверяется по заголовку, а
не по расширению); тип 0 молча ничего не создаёт.

Инструмент — для мультимодальной проверки: посмотреть на схему глазами (как
на изображение), а не только на текстовую выгрузку топологии
(`export_model_text`) или `.xprt`. Файл пишется в каталог результатов
(песочница), имя уникально на вызов — серия снимков «до/после» не затирает
предыдущие.
"""

from __future__ import annotations

import os
from typing import Dict

from fastmcp.exceptions import ToolError
from simintech_api.exceptions import ScriptBridgeError
from simintech_api.script_probe import OUTCOME_MODEL_NOT_RUNNING

from .. import runtime, sandbox
from ..app import mcp
from .model_text import bridge
from .page_script import (
    describe_outcome,
    discard_result,
    fresh_name,
    refuse_on_bad_outcome,
    result_path,
)

#: Форматы `savescreenshot`: имя → код типа в вызове (справка поставки).
FORMATS: Dict[str, int] = {"png": 2, "bmp": 1, "svg": 3}

#: Формат по умолчанию. PNG сжат (десятки килобайт против мегабайт BMP) и
#: читается мультимодальными клиентами напрямую — снимок делается, чтобы на
#: него смотрели.
DEFAULT_FORMAT = "png"


def build_screenshot_body(path: str, type_code: int) -> str:
    """Тело контура: `savescreenshot("<путь>", <тип>)`.

    Путь подставляется в литерал встроенного языка: кавычка или перевод
    строки в нём сломали бы скрипт, поэтому отвергаются — как у остальных
    тел контура.
    """
    literal = path.replace("\\", "/")
    if '"' in literal or "\n" in literal or "\r" in literal:
        raise ToolError(
            "путь снимка не может содержать кавычку или перевод строки: "
            f"{path!r} — он подставляется в литерал встроенного языка.")
    return f'savescreenshot("{literal}", {type_code});'


@mcp.tool()
@runtime.com_threaded
def save_screenshot(format: str = DEFAULT_FORMAT) -> str:
    """Сохранить снимок текущего вида схемы в файл (PNG/BMP/SVG) — и посмотреть глазами.

    Это проверка того, чего не видно в текстовой выгрузке
    (`export_model_text`): читаемость раскладки, наложения, ортогональность
    линий. Выгрузка после нормализации даёт те же точки линий бит-в-бит
    (нормализация — свойство **отрисовки**, живой замер 03.10.2026), а снимок
    показывает фактический вид; для MR-процесса это снимки «до/после».
    Мультимодальный клиент открывает файл как изображение.

    **Как это работает.** Тело `savescreenshot(...)` исполняется в секции
    `initialization` через контур: расчёт делает несколько шагов, **модельное
    время сдвигается** (как после `step`). Снимок есть и на несчитающей
    модели — функция пишется из `initialization`, а не из шагов расчёта; исход
    назовётся в ответе.

    **Файл** — в каталоге результатов, имя уникально на вызов
    (`screenshot-<8 hex>.<формат>`): серия снимков «до/после» не затирает
    предыдущие. Все три формата подтверждены живым замером: PNG — настоящий
    PNG (в разы легче BMP), SVG — вектор.

    Args:
        format: «png» (по умолчанию), «bmp» или «svg».
    """
    key = format.strip().lower()
    type_code = FORMATS.get(key)
    if type_code is None:
        raise ToolError(
            f"формат {format!r} не поддерживается: известны "
            f"{', '.join(FORMATS)} (коды типов функции savescreenshot: "
            f"{', '.join(f'{name}={code}' for name, code in FORMATS.items())})."
        )

    root = sandbox.output_root()
    shot_path = os.path.join(root, fresh_name(f"screenshot.{key}"))
    contour_path = result_path()
    body = build_screenshot_body(shot_path, type_code)

    try:
        run = bridge().run_page_script(body, contour_path)
    except ScriptBridgeError as exc:
        discard_result(contour_path)
        raise ToolError(
            f"снимок не сделан: {exc}. Тело идёт в секцию `initialization`: "
            "проверьте, что модель считает — неподключённый вход "
            "останавливает расчёт всей модели молча."
        ) from exc
    discard_result(contour_path)
    outcome = run.outcome
    refuse_on_bad_outcome(outcome, action="съёмка схемы")

    # Файла может не быть, даже если тело «отработало»: на неподходящем типе
    # savescreenshot молча ничего не создаёт (замер 03.10.2026: тип 0) — это
    # состояние, которое обязано стать отказом, а не «снимок: путь» без файла.
    if not os.path.isfile(shot_path) or os.path.getsize(shot_path) == 0:
        raise ToolError(
            f"скрипт отработал, но файла снимка нет: {shot_path}. "
            f"savescreenshot формата «{key}» ничего не записал — проверьте "
            "поддержку формата в этой сборке."
        )

    size = os.path.getsize(shot_path)
    head = ""
    if outcome.kind == OUTCOME_MODEL_NOT_RUNNING:
        head = (describe_outcome(outcome, what="Снимок")
                + " Снимок при этом есть: savescreenshot пишется из секции "
                  "`initialization`, а не из шагов расчёта.\n")
    return (f"{head}Снимок схемы ({key}): {shot_path} ({size} байт). "
            "Откройте файл как изображение — это фактический вид схемы.")
