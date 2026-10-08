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

**Размер снимка — это размер окна.** Живой замер 08.10.2026 (2.26.9.29):
сохранённое изображение равно клиентской области окна графического
контейнера — минус 32 px по ширине и 200 по высоте (постоянно на всех
замерах: окно 1920×903 → PNG 1888×703, окно 3072×1100 → 3040×900). Отсюда и
«плавающее» полотно у прошлых снимков (1026×580 против 1888×703) — это были
разные окна. Поэтому у одиночного снимка больших схем подписи мелкие: схема
высокой формы вписывается в широкое низкое окно.

**Высокое качество.** Два режима поднимают окно сами (`normalizeform` +
`setformbounds(0, 0, 4000, 1100)`; без `normalizeform` на развёрнутом окне
`setformbounds` молча без эффекта, а применённое видно со **следующего**
прогона — асинхронность замера) и возвращают прежние границы окна после
съёмки:

* `hires=True` — один файл на максимальном полотне (клиент до 3968×900;
  выше мешает кламп высоты окна — 1100);
* `zoom=1.0` — снимок крупным планом: сетка плиток по рамке модели в
  заданном масштабе (1 — 1:1, как вид в GUI), каждая плитка своим файлом;
  живой замер: схема 1672×2368 — три плитки 1696×900, все подписи читаются.

А для **вектора** размер не нужен вовсе: `format="svg"` отдаёт настоящий
вектор (подписи — элементы `<text>`, замер: 217 штук), качество не
ограничено окном. У корня SVG нет `width`/`height`/`viewBox` — простые
вьюеры покажут его мелким (300×150) или обрезанным, поэтому крупный растр
из него рендерят отдельно: браузером или, например,
`inkscape --export-area-drawing --export-width=4000` (живой рендер:
PNG 4000×5472, подписи читаются).
"""

from __future__ import annotations

import math
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from fastmcp.exceptions import ToolError
from simintech_api.script_probe import ContourOutcome, OUTCOME_MODEL_NOT_RUNNING

from .. import runtime, sandbox, session
from ..app import mcp
from .page_script import (
    describe_outcome,
    fresh_name,
    refuse_contour_failure,
    run_contour,
)

#: Форматы `savescreenshot`: имя → код типа в вызове (справка поставки).
FORMATS: Dict[str, int] = {"png": 2, "bmp": 1, "svg": 3}

#: Формат по умолчанию. PNG сжат (десятки килобайт против мегабайт BMP) и
#: читается мультимодальными клиентами напрямую — снимок делается, чтобы на
#: него смотрели.
DEFAULT_FORMAT = "png"

#: Целевое окно режимов `hires`/`zoom` — живой замер 08.10.2026 (2.26.9.29):
#: окно 4000×1100 среда приняла; высота 1100 — потолок клампа (клиент 900),
#: его не пробивают ни `setformbounds` с высотой 1728/2000/3000, ни
#: `setformsize(3968, 2000)`; ширина 4000 >= проверенной. Больше заказывать
#: незачем: кламп срежет, а лишняя ширина пикселей не добавит.
HIRES_WINDOW_W = 4000
HIRES_WINDOW_H = 1100

#: Поправка «окно → снимок» — тот же замер: PNG равен клиентской области
#: окна, минус 32 px по ширине и 200 по высоте (постоянно: 1920×903→1888×703,
#: 3072×1100→3040×900, 3968×1100→3936×900).
WINDOW_BORDER_W = 32
WINDOW_CHROME_H = 200

#: Объявление переменных для `getformbounds` — форма из справки поставки
#: («var L: integer, T: integer, …»). Запись короче (`var L, T, W, H:
#: integer;`) живым замером 08.10.2026 отвергнута: not-compiled.
_BOUNDS_VARS = "var L: integer, T: integer, W: integer, H: integer;\n"

#: Плитки режима `zoom`: перекрытие соседних (единицы модели) — стык без
#: разрыва на границе; предел числа — каждая плитка это прогон контура
#: (~20 с), и десятки прогонов недопустимы.
TILE_MARGIN = 32.0
MAX_TILES = 16

#: Границы параметра `zoom`: ниже — плитка мельче полезного (подписи слились
#: бы, проще взять `hires`), выше — пиксель дороже единицы модели вчетверо;
#: растровому снимку это уже не добавляет деталей.
ZOOM_MIN = 0.1
ZOOM_MAX = 4.0


#: Фактический размер полотна последнего снимка (ширина, высота).
#: Размер PNG у среды не постоянен (живой замер 05.10.2026: 1026x580 у части
#: снимков и 1026x659 у другой), а `container_width`/`container_height` в контуре
#: не компилируются. Поэтому размер узнаётся по самому снимку, а не угадывается.
#: Имя в нижнем регистре: pyright strict не даёт переопределять
#: глобальное имя в ВЕРХНЕМ регистре — оно считается константой
#: (`reportConstantRedefinition`), а размер обновляется на каждом снимке.
_last_canvas: "Optional[Tuple[int, int]]" = None


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


def _path_literal(path: str) -> str:
    """Путь снимка литералом встроенного языка — с проверкой кавычек.

    Кавычка или перевод строки в пути сломали бы скрипт, поэтому отвергаются
    до COM-вызова — как у остальных тел контура.
    """
    literal = path.replace("\\", "/")
    if '"' in literal or "\n" in literal or "\r" in literal:
        raise ToolError(
            "путь снимка не может содержать кавычку или перевод строки: "
            f"{path!r} — он подставляется в литерал встроенного языка.")
    return literal


def build_screenshot_body(path: str, type_code: int) -> str:
    """Тело контура: `savescreenshot("<путь>", <тип>)`."""
    return f'savescreenshot("{_path_literal(path)}", {type_code});'


def build_framed_shot_body(path: str, type_code: int,
                           view_x: float, view_y: float,
                           scale: float) -> str:
    """Тело контура: кадр страницы и снимок — **одним прогоном**.

    Замер 08.10.2026: PNG после такого тела байт-в-байт совпал со снимком,
    снятым после отдельной записи кадра (`md5` тот же) — кадр успевает
    примениться до `savescreenshot` внутри прогона, и плитка стоит один
    прогон контура, а не два. Семантика свойств — как у `fit_geometry`:
    экран = модель × масштаб + смещение.
    """
    return (
        "const model : ("
        f"x_center = {view_x:g}, y_center = {view_y:g}, "
        f"x_scale = {scale:g}, y_scale = {scale:g});\n"
        "createmodel(getcurrentprojectid, model);\n"
        f'savescreenshot("{_path_literal(path)}", {type_code});'
    )


def _bounds_body() -> str:
    """Тело контура: прочитать границы окна — строка `bounds=L,T,W,H`."""
    return (
        _BOUNDS_VARS
        + "getformbounds(L, T, W, H);\n"
        + 'writelnutf8(fid, "bounds=" + inttostr(L) + "," + inttostr(T)'
          ' + "," + inttostr(W) + "," + inttostr(H));'
    )


def _raise_window_body() -> str:
    """Тело контура: границы «до» и подъём окна до целевого — один прогон.

    Поднимаем, только если окно меньше целевого: уже большое окно не
    уменьшаем (лишние пиксели не мешают, а вид окна — состояние проекта, и
    трогать его без нужды незачем). `normalizeform` обязателен перед
    `setformbounds`: на развёрнутом окне тот молча без эффекта (замер
    08.10.2026). Строка `before=…` — для возврата окна после съёмки.
    """
    return (
        _BOUNDS_VARS
        + "getformbounds(L, T, W, H);\n"
        + 'writelnutf8(fid, "before=" + inttostr(L) + "," + inttostr(T)'
          ' + "," + inttostr(W) + "," + inttostr(H));\n'
        + f"if (W < {HIRES_WINDOW_W}) or (H < {HIRES_WINDOW_H}) then begin\n"
        + "  normalizeform;\n"
        + f"  setformbounds(0, 0, {HIRES_WINDOW_W}, {HIRES_WINDOW_H});\n"
        + '  writelnutf8(fid, "raised");\n'
        + "end;"
    )


def _restore_window_body(bounds: "Tuple[int, int, int, int]") -> str:
    """Тело контура: вернуть окну прежние границы."""
    left, top, width, height = bounds
    return f"setformbounds({left}, {top}, {width}, {height});"


def _bounds_from_lines(lines: Sequence[str],
                       prefix: str) -> "Optional[Tuple[int, int, int, int]]":
    """Границы из строк тела: `bounds=` или `before=` — `None`, если нет."""
    pattern = re.compile(re.escape(prefix) + r"(-?\d+),(-?\d+),(\d+),(\d+)")
    for line in lines:
        match = pattern.search(line)
        if match:
            return (int(match.group(1)), int(match.group(2)),
                    int(match.group(3)), int(match.group(4)))
    return None


def _axis_positions(low: float, high: float, span: float,
                    margin: float) -> "List[float]":
    """Центры плиток по одной оси: покрыть [low, high] отрезками `span`.

    Первая плитка начинается на `low`, последняя кончается на `high`, между
    соседними остаётся перекрытие `margin` — стык без разрыва (объект на
    границе попадёт в обе плитки, а не в щель). Один отрезок — центр рамки.
    """
    size = high - low
    if size <= span or span <= margin:
        return [(low + high) / 2.0]
    step = span - margin
    count = max(int(math.ceil((size - margin) / step)), 1)
    if count == 1:
        return [(low + high) / 2.0]
    return [low + (size - span) * index / (count - 1) + span / 2.0
            for index in range(count)]


def tile_centers(frame: "Tuple[float, float, float, float]",
                 client_w: float, client_h: float, zoom: float,
                 margin: float = TILE_MARGIN) -> "List[Tuple[float, float]]":
    """Центры плиток (координаты модели): сверху вниз, слева вправо.

    Плитка охватывает `client / zoom` единиц модели — при `zoom = 1` это
    масштаб 1:1 (пиксель полотна на единицу модели, как вид в GUI).
    """
    left, top, right, bottom = frame
    span_x = client_w / zoom
    span_y = client_h / zoom
    xs = _axis_positions(left, right, span_x, margin)
    ys = _axis_positions(top, bottom, span_y, margin)
    return [(x, y) for y in ys for x in xs]


def _tile_span(client: float, zoom: float) -> float:
    """Охват плитки в единицах модели по одной оси."""
    return client / zoom


def _frame_or_error() -> "Tuple[float, float, float, float]":
    """Рамка модели для крупных режимов — или отказ.

    `model_frame` — тот же расчёт, что у `fit_geometry` и метрики наложений:
    объединение габаритов блоков (`Points` + `get_size`). `None` — считать
    кадр и плитки не по чему, и молча снять «как получится» значило бы
    выдать неизвестно что за качественный снимок.
    """
    from .layout import model_frame

    frame = model_frame(session.ensure_project().get_main_page())
    if frame is None:
        raise ToolError(
            "рамка модели не читается: на странице нет блоков с габаритами "
            "(`Points` и `get_size`) — считать кадр не по чему.")
    return frame


def _shoot_into(path: str, body: str) -> ContourOutcome:
    """Сделать снимок телом контура; файл обязан появиться."""
    outcome, _restored = run_contour(body, failed="снимок не сделан")
    refuse_contour_failure(outcome, failed="съёмка схемы")
    # Файла может не быть, даже если тело «отработало»: на неподходящем
    # типе savescreenshot молча ничего не создаёт (замер 03.10.2026, тип 0).
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise ToolError(
            f"скрипт отработал, но файла снимка нет: {path}. "
            "savescreenshot ничего не записал — проверьте поддержку "
            "формата в этой сборке."
        )
    return outcome


def _shot_head(outcome: ContourOutcome) -> str:
    """Префикс ответа: снимок есть и на несчитающей модели — сказать это."""
    if outcome.kind == OUTCOME_MODEL_NOT_RUNNING:
        return (describe_outcome(outcome, what="Снимок")
                + " Снимок при этом есть: savescreenshot пишется из секции "
                  "`initialization`, а не из шагов расчёта.\n")
    return ""


def _raise_window() -> "Tuple[bool, Tuple[int, int, int, int]]":
    """Поднять окно до целевого; вернуть (поднимали, границы «до»).

    Границы «до» нужны, чтобы вернуть окно после съёмки; без них (тело не
    отдало строку `before=…`) — отказ: окно осталось бы раздутым, вернуть
    его прежним было бы нечем.
    """
    outcome, _restored = run_contour(
        _raise_window_body(), failed="поднять окно под снимок не удалось")
    refuse_contour_failure(outcome, failed="подъём окна")
    before = _bounds_from_lines(outcome.lines, "before=")
    if before is None:
        raise ToolError(
            "границы окна не прочитались: тело контура не отдало строку "
            "`before=…` — поднимать окно вслепую нельзя.")
    raised = any(line.strip() == "raised" for line in outcome.lines)
    return raised, before


def _read_window_bounds() -> "Optional[Tuple[int, int, int, int]]":
    """Фактические границы окна — после подъёма (он асинхронен)."""
    outcome, _restored = run_contour(
        _bounds_body(), failed="прочитать границы окна не удалось")
    refuse_contour_failure(outcome, failed="чтение границ окна")
    return _bounds_from_lines(outcome.lines, "bounds=")


def _restore_window(bounds: "Tuple[int, int, int, int]") -> "Optional[str]":
    """Вернуть окну прежние границы; `None` — удалось, иначе текст отказа.

    Отказ возврата — не отказ инструмента: снимок уже сделан, и терять его
    из-за окна нельзя; но молчать нельзя вдвойне — окно осталось бы чужим.
    """
    try:
        outcome, _restored = run_contour(
            _restore_window_body(bounds), failed="вернуть окно не удалось")
        refuse_contour_failure(outcome, failed="возврат окна")
    except Exception as exc:  # noqa: BLE001
        return (f"ВНИМАНИЕ: окно вернуть не удалось "
                f"({type(exc).__name__}: {exc}) — границы, выставленные под "
                f"снимок, могли сохраниться.")
    return None


def _client_of(bounds: "Tuple[int, int, int, int]") -> "Tuple[float, float]":
    """Клиентская область окна — это и есть полотно снимка (−32/−200)."""
    return (max(float(bounds[2] - WINDOW_BORDER_W), 1.0),
            max(float(bounds[3] - WINDOW_CHROME_H), 1.0))


def _large_shot(root: str, key: str, type_code: int, fit: bool,
                zoom: Optional[float]) -> str:
    """Режимы `hires`/`zoom`: поднять окно, снять, вернуть окно прежним.

    Подъём виден со следующего прогона (асинхронность, замер 08.10.2026),
    поэтому фактические границы читаются отдельным прогоном: кадр и плитки
    обязаны считаться по фактической клиентской области, а не по заказанной
    (окно может клампнуться на другой машине).
    """
    raised, before = _raise_window()
    if raised:
        after = _read_window_bounds() or before
    else:
        after = before
    client_w, client_h = _client_of(after)

    notes: List[str] = []
    if raised:
        notes.append(
            f"Окно под снимок: {before[2]}x{before[3]} → {after[2]}x"
            f"{after[3]}; клиент (полотно) — {client_w:.0f}x{client_h:.0f}.")
    else:
        notes.append(
            f"Окно не меньше целевого: {after[2]}x{after[3]}; клиент "
            f"(полотно) — {client_w:.0f}x{client_h:.0f}.")

    try:
        if zoom is None:
            text = _hires_shot(root, key, type_code, fit, client_w, client_h)
        else:
            text = _tiles_shot(root, key, type_code, zoom, client_w, client_h)
    finally:
        # Возврат окна — в `finally`: отказ в середине съёмки (предел плиток,
        # обрыв контура) не имеет права оставить окно раздутым — это
        # состояние проекта, чужое для вызывающего.
        if raised:
            failure = _restore_window(before)
            notes.append(failure if failure else
                         f"Окно возвращено: {before[2]}x{before[3]} "
                         "(разворот, если он был, вернёт `maximizeform`).")

    return text + "\n" + "\n".join(notes)


def _hires_shot(root: str, key: str, type_code: int, fit: bool,
                client_w: float, client_h: float) -> str:
    """Один снимок на поднятом полотне; кадр — вписанная по рамке модель."""
    path = os.path.join(root, fresh_name(f"screenshot.{key}"))
    note = ""
    if not fit:
        outcome = _shoot_into(path, build_screenshot_body(path, type_code))
    else:
        from .layout import fit_geometry

        frame = _frame_or_error()
        scale, view_x, view_y = fit_geometry(frame, client_w, client_h)
        outcome = _shoot_into(path, build_framed_shot_body(
            path, type_code, view_x, view_y, scale))
        actual = _png_size(path) if key == "png" else None
        if actual is not None and (abs(actual[0] - client_w) > 1.0
                                   or abs(actual[1] - client_h) > 1.0):
            # Предсказанный клиент разошёлся с фактом — кадр пересчитывается
            # под фактическое полотно (тем же приёмом: кадр и снимок в одном
            # прогоне). Один повтор, дальше расхождению веры нет.
            scale, view_x, view_y = fit_geometry(
                frame, float(actual[0]), float(actual[1]))
            outcome = _shoot_into(path, build_framed_shot_body(
                path, type_code, view_x, view_y, scale))
            actual = _png_size(path) or actual
            note = (f"\nПолотно — {actual[0]}x{actual[1]} (клиент окна "
                    "разошёлся с предсказанным): кадр пересчитан, снимок "
                    "сделан заново.")
    size = os.path.getsize(path)
    return (f"{_shot_head(outcome)}Снимок схемы ({key}): {path} ({size} "
            f"байт).{note} "
            "Откройте файл как изображение — это фактический вид схемы.")


def _tiles_shot(root: str, key: str, type_code: int, zoom: float,
                client_w: float, client_h: float) -> str:
    """Плитки: сетка по рамке модели, каждая плитка — свой прогон и файл.

    Плитка снимается одним прогоном (`build_framed_shot_body` — кадр и
    снимок в одном теле, замер 08.10.2026: md5 совпал со снимком после
    отдельной записи кадра).
    """
    frame = _frame_or_error()
    width = frame[2] - frame[0]
    height = frame[3] - frame[1]
    centers = tile_centers(frame, client_w, client_h, zoom)
    if len(centers) > MAX_TILES:
        raise ToolError(
            f"плиток {len(centers)} — больше предела {MAX_TILES}: при "
            f"zoom={zoom:g} плитка охватывает {client_w / zoom:.0f}x"
            f"{client_h / zoom:.0f} единиц модели, а схема — {width:.0f}x"
            f"{height:.0f}. Уменьшите zoom (плитка охватит больше, но и "
            "мельче станет), либо снимайте по `hires` одним полотном.")
    span_x = _tile_span(client_w, zoom)
    span_y = _tile_span(client_h, zoom)
    parts: List[str] = []
    outcome: Optional[ContourOutcome] = None
    for index, (cx, cy) in enumerate(centers, start=1):
        path = os.path.join(root, fresh_name(
            f"screenshot-{index}of{len(centers)}.{key}"))
        view_x = client_w / 2.0 - cx * zoom
        view_y = client_h / 2.0 - cy * zoom
        outcome = _shoot_into(path, build_framed_shot_body(
            path, type_code, view_x, view_y, zoom))
        parts.append(
            f"  {index}/{len(centers)}: x {cx - span_x / 2:.0f}.."
            f"{cx + span_x / 2:.0f}, y {cy - span_y / 2:.0f}.."
            f"{cy + span_y / 2:.0f} ед. модели — {path} "
            f"({os.path.getsize(path)} байт)")
    tail = ("\nКадр страницы оставлен на последней плитке — вписанный вид "
            "вернёт `fit_view`." if len(centers) > 1 else "")
    return (f"{_shot_head(outcome) if outcome else ''}Снимок схемы ({key}) "
            f"плитками: {len(centers)} шт., масштаб {zoom:g} (плитка — "
            f"{span_x:.0f}x{span_y:.0f} ед. модели).\n"
            + "\n".join(parts) + tail)


@mcp.tool()
@runtime.com_threaded
def save_screenshot(format: str = DEFAULT_FORMAT, fit: bool = True,
                    hires: bool = False,
                    zoom: Optional[float] = None) -> str:
    """Сохранить снимок текущего вида схемы в файл (PNG/BMP/SVG) — и посмотреть глазами.

    Это проверка того, чего не видно в текстовой выгрузке
    (`export_model_text`): читаемость раскладки, наложения, ортогональность
    линий. У линий с готовым маршрутом выгрузка после нормализации даёт те
    же точки бит-в-бит (живой замер 03.10.2026), а пустые маршруты
    нормализация заполняет (замер 04.10.2026) — по выгрузке нормализацию не
    проверить: снимок показывает фактический вид; для MR-процесса это снимки
    «до/после».
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

    **Высокое качество (`hires`, `zoom`).** Размер снимка — это размер окна
    (клиентская область, −32/−200; замер 08.10.2026), поэтому оба режима
    поднимают окно до 4000×1100 (`normalizeform` обязателен: на развёрнутом
    окне `setformbounds` молча без эффекта) и после съёмки возвращают прежние
    границы.

    * `hires=True` — один файл на максимальном полотне (клиент до 3968×900):
      для схемы, которой тесно в обычном окне (1026×580 или 1888×703 —
      какие окна застали замеры).
    * `zoom=1.0` — снимок крупным планом плитками: сетка по рамке модели в
      заданном масштабе (1 — 1:1, как вид в GUI), каждая плитка своим файлом
      `screenshot-<N>of<M>.<формат>` с перекрытием на стыках; предел — 16
      плиток (каждая — прогон контура). Живой замер: схема 1672×2368 — три
      плитки 1696×900, все подписи читаются. Кадр `fit` в этом режиме не
      участвует, а после съёмки кадр страницы остаётся на последней плитке
      (вписанный вид вернёт `fit_view`).

    SVG — вектор: полотно его не ограничивает, поэтому `hires`/`zoom` к нему
    неприменимы (отказ с подсказкой). Крупный растр из SVG рендерится вне
    сервера: браузером или `inkscape --export-area-drawing
    --export-width=4000` (живой рендер 4000×5472, подписи читаются).

    Args:
        format: «png» (по умолчанию), «bmp» или «svg».
        fit: True (по умолчанию) — подогнать кадр по рамке модели перед
            съёмкой; False — снять текущий вид как есть.
        hires: True — поднять окно под максимальное полотно и вернуть его
            после снимка (для одного файла). С `zoom` не сочетается.
        zoom: масштаб плиток (1 — 1:1) — снимок крупным планом в нескольких
            файлах; `None` (по умолчанию) — обычный одиночный снимок.
    """
    global _last_canvas

    key = format.strip().lower()
    type_code = FORMATS.get(key)
    if type_code is None:
        raise ToolError(
            f"формат {format!r} не поддерживается: известны "
            f"{', '.join(FORMATS)} (коды типов функции savescreenshot: "
            f"{', '.join(f'{name}={code}' for name, code in FORMATS.items())})."
        )

    if hires and zoom is not None:
        raise ToolError(
            "выберите один режим: `hires` (одно большое полотно) или `zoom` "
            "(плитки крупным планом) — они по-разному строят кадр.")
    if zoom is not None and not (ZOOM_MIN <= zoom <= ZOOM_MAX):
        raise ToolError(
            f"zoom={zoom:g} вне пределов {ZOOM_MIN:g}..{ZOOM_MAX:g}: меньше — "
            "плитка мельче одиночного снимка (смотрите `hires`), больше — "
            "растру это деталей не добавит.")
    if (hires or zoom is not None) and key == "svg":
        raise ToolError(
            "SVG — вектор: полотно окна его не ограничивает, `hires`/`zoom` "
            "ему не нужны. Крупный растр из вектора рендерится вне сервера "
            "(браузер, `inkscape --export-width=...`).")

    if hires or zoom is not None:
        # Обычный снимок _last_canvas не трогает: окно возвращается прежним,
        # и память о полотне обычных снимков остаётся верной.
        return _large_shot(sandbox.output_root(), key, type_code, fit, zoom)

    canvas = _last_canvas
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

    def _shoot() -> "Tuple[ContourOutcome, str]":
        """Сделать снимок и вернуть (исход контура, путь файла)."""
        path = os.path.join(root, fresh_name(f"screenshot.{key}"))
        return (_shoot_into(path, build_screenshot_body(path, type_code)),
                path)

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
            except Exception as exc:  # noqa: BLE001
                canvas_note = (f"\nПолотно снимка — {actual[0]}x{actual[1]}, "
                               "кадр под него пересчитать не удалось: "
                               f"{type(exc).__name__}: {exc}")
            else:
                # Отдельно от пересчёта: пересчёт мог удаться, а упасть —
                # повторная съёмка, и примечание обязано называть именно её.
                try:
                    outcome, shot_path = _shoot()
                    actual = _png_size(shot_path) or actual
                    canvas_note = (f"\nПолотно снимка — {actual[0]}x{actual[1]}: "
                                   "кадр пересчитан под него, снимок сделан "
                                   "заново.")
                except Exception as exc:  # noqa: BLE001
                    canvas_note = (f"\nПолотно снимка — {actual[0]}x{actual[1]}: "
                                   "кадр пересчитан, но повторная съёмка не "
                                   f"удалась: {type(exc).__name__}: {exc}")
        _last_canvas = actual

    size = os.path.getsize(shot_path)
    return (f"{_shot_head(outcome)}Снимок схемы ({key}): {shot_path} "
            f"({size} байт).{fit_note}{canvas_note} "
            "Откройте файл как изображение — это фактический вид схемы.")
