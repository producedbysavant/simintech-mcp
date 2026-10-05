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
from typing import Any, Dict, Optional, Tuple

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


#: Фактический размер полотна последнего снимка (ширина, высота).
#: Размер PNG у среды не постоянен (живой замер 05.10.2026: 1026x580 у части
#: снимков и 1026x659 у другой), а `container_width`/`container_height` в контуре
#: не компилируются. Поэтому размер узнаётся по самому снимку, а не угадывается.
_LAST_CANVAS: "Optional[Tuple[int, int]]" = None


def _png_size(path: str) -> "Optional[Tuple[int, int]]":
    """Размер PNG из заголовка IHDR — без сторонних библиотек.

    Расширение файла формат не выбирает (замер 03.10.2026), поэтому проверяется
    и подпись PNG, а не только имя: у BMP и SVG размер здесь не читается, и
    функция честно возвращает `None` — вызывающий тогда ничего не пересчитывает.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (int.from_bytes(head[16:20], "big"),
            int.from_bytes(head[20:24], "big"))


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
def save_screenshot(format: str = DEFAULT_FORMAT, fit: bool = True) -> str:
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

    **Кадр подгоняется сам** (`fit=True`). Перед съёмкой считается рамка модели
    (объединение габаритов блоков) и в свойства страницы пишутся центр и
    масштаб — тот же шаг, что делает `fit_view`. Без него снимок показывал угол
    схемы: раскладка начинается с (0, 0), а вид стоял в масштабе 1:1 (живой
    случай 04.10.2026 — модель занимала около 5% полотна, левые блоки срезаны
    краем листа).

    `fit=False` — сырой текущий вид: нужен, когда важна именно та картинка, что
    видит человек, без правки кадра. Если габариты прочитать не удалось, снимок
    всё равно делается — в ответе будет примечание, а не отказ.

    Args:
        format: «png» (по умолчанию), «bmp» или «svg».
        fit: True (по умолчанию) — подогнать кадр по рамке модели перед
            съёмкой; False — снять текущий вид как есть.
    """
    key = format.strip().lower()
    type_code = FORMATS.get(key)
    if type_code is None:
        raise ToolError(
            f"формат {format!r} не поддерживается: известны "
            f"{', '.join(FORMATS)} (коды типов функции savescreenshot: "
            f"{', '.join(f'{name}={code}' for name, code in FORMATS.items())})."
        )

    canvas = _LAST_CANVAS
    canvas_note = ""
    fit_note = ""
    if fit:
        from .layout import apply_fit_view

        try:
            kwargs = ({"canvas_w": float(canvas[0]), "canvas_h": float(canvas[1])}
                      if canvas else {})
            lines = apply_fit_view(**kwargs).splitlines()
            fit_note = "\n" + "\n".join(lines[:2])
        except Exception as exc:  # noqa: BLE001
            # Подгонка — не условие съёмки: её неудача обязана стать
            # примечанием, а не отказом. Снимок сырого вида полезнее, чем
            # ничего, и клиент видит, что кадр не подогнан (и почему).
            fit_note = f"\nКадр не подогнан: {type(exc).__name__}: {exc}"

    root = sandbox.output_root()

    def _shoot() -> "Tuple[Any, str]":
        """Сделать снимок и вернуть (исход контура, путь файла)."""
        path = os.path.join(root, fresh_name(f"screenshot.{key}"))
        contour = result_path()
        try:
            run = bridge().run_page_script(
                build_screenshot_body(path, type_code), contour)
        except ScriptBridgeError as exc:
            discard_result(contour)
            raise ToolError(
                f"снимок не сделан: {exc}. Тело идёт в секцию `initialization`: "
                "проверьте, что модель считает — неподключённый вход "
                "останавливает расчёт всей модели молча."
            ) from exc
        discard_result(contour)
        refuse_on_bad_outcome(run.outcome, action="съёмка схемы")
        # Файла может не быть, даже если тело «отработало»: на неподходящем
        # типе savescreenshot молча ничего не создаёт (замер 03.10.2026, тип 0).
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise ToolError(
                f"скрипт отработал, но файла снимка нет: {path}. "
                f"savescreenshot формата «{key}» ничего не записал — "
                "проверьте поддержку формата в этой сборке."
            )
        return (run.outcome, path)

    outcome, shot_path = _shoot()

    # Полотно снимка у среды не постоянно (живой замер 05.10.2026: 1026x580 у
    # части снимков и 1026x659 у другой). Поэтому фактический размер читается из
    # самого PNG: разошёлся с тем, по которому считали кадр, — кадр
    # пересчитывается и снимок делается заново, один раз.
    actual = _png_size(shot_path) if key == "png" else None
    if actual is not None:
        if fit and canvas != actual:
            from .layout import apply_fit_view

            try:
                lines = apply_fit_view(canvas_w=float(actual[0]),
                                       canvas_h=float(actual[1])).splitlines()
                fit_note = "\n" + "\n".join(lines[:2])
                outcome, shot_path = _shoot()
                actual = _png_size(shot_path) or actual
                canvas_note = (f"\nПолотно снимка — {actual[0]}x{actual[1]}: "
                               "кадр пересчитан под него, снимок сделан заново.")
            except Exception as exc:  # noqa: BLE001
                canvas_note = (f"\nПолотно снимка — {actual[0]}x{actual[1]}, "
                               "кадр под него пересчитать не удалось: "
                               f"{type(exc).__name__}: {exc}")
        globals()["_LAST_CANVAS"] = actual

    size = os.path.getsize(shot_path)
    head = ""
    if outcome.kind == OUTCOME_MODEL_NOT_RUNNING:
        head = (describe_outcome(outcome, what="Снимок")
                + " Снимок при этом есть: savescreenshot пишется из секции "
                  "`initialization`, а не из шагов расчёта.\n")
    return (f"{head}Снимок схемы ({key}): {shot_path} ({size} байт)."
            f"{fit_note}{canvas_note} "
            "Откройте файл как изображение — это фактический вид схемы.")
