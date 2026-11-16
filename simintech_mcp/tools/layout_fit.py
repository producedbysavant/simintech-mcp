"""Подгонка кадра схемы: формула масштаба и запись свойств страницы.

Модуль выделен из `layout` без изменения поведения: `fit_geometry` считает
кадр по рамке модели, `apply_fit_view` пишет его в свойства страницы. Обе
функции нужны и инструменту `fit_view`, и `save_screenshot`.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from .. import session
from .layout_geometry import EM_DASH, model_frame


#: Полотно снимка схемы (живой замер 04.10.2026): `savescreenshot` отдаёт PNG
#: 1026x580. По нему считается масштаб подгонки кадра.
CANVAS_W = 1026.0
CANVAS_H = 580.0

#: Поля подгонки кадра: доля полотна, оставляемая по краям.
FIT_PADDING = 0.06


def fit_geometry(frame: "tuple[float, float, float, float]",
                 canvas_w: float = CANVAS_W,
                 canvas_h: float = CANVAS_H,
                 padding: float = FIT_PADDING) -> "tuple[float, float, float]":
    """Кадр по рамке модели: (масштаб, смещение_x, смещение_y).

    Семантика свойств кадра по живому замеру 04.10.2026 (разбор пикселей
    снимков): **экран = модель * масштаб + смещение**. То есть
    `x_center`/`y_center` - смещение В ПИКСЕЛЯХ, а не координата модели: при
    0/0 и масштабе 1 снимок совпал с координатами модели точка в точку
    (габарит тёмных точек 48..561 при модели 48..560), при масштабе 4 левый
    край встал на 192 = 48*4.

    Масштаб берётся по меньшей из сторон: модель обязана войти целиком, и
    растянуть её под полотно нельзя - иначе стороны разъедутся. Поля
    `padding` (доля полотна) остаются с обоих краёв.

    Функция чистая - ни COM, ни состояния, - поэтому её и проверяют тесты:
    `tests/unit/test_layout_tools.py`, класс `test_fit_geometry_*`.
    """
    left, top, right, bottom = frame
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    scale = min(canvas_w * (1.0 - 2.0 * padding) / width,
                canvas_h * (1.0 - 2.0 * padding) / height)
    return (scale,
            canvas_w / 2.0 - cx * scale,
            canvas_h / 2.0 - cy * scale)


def apply_fit_view(canvas_w: float = CANVAS_W,
                   canvas_h: float = CANVAS_H) -> str:
    """Посчитать рамку модели и выставить кадр страницы - общий шаг подгонки.

    Нужен обоим: и инструменту `fit_view`, и `save_screenshot` - снимок обязан
    показывать модель целиком, а не её угол. Возвращает краткую сводку; если
    габариты прочитать не удалось, бросает `ToolError` (вызывающий сам решает,
    отказ это или примечание к снимку).

    Показать схему целиком: посчитать рамку модели и выставить кадр страницы.

    Готовой функции «показать целиком» в языке среды нет - проверено по справке
    04.10.2026: `changeprojectzoom` это загрузка проекта из файла, а раздел «слои
    схемного окна» про видимость слоёв. Поэтому рамку считает сам инструмент,
    а кадр пишется в свойства страницы.

    **Как считается.** Габариты всех блоков страницы объединяются в одну рамку
    (центр из `Points` плюс `get_size`; подписи пропускаются), её центр
    становится `x_center`/`y_center`, а масштаб - отношением полотна снимка к
    рамке с полями `FIT_PADDING`. Полотно 1026x580 - живой замер PNG от
    `savescreenshot`. Фактический размер узнаёт `save_screenshot` по самому
    снимку (он у среды не постоянен: живой замер 05.10.2026 - 1026x580 у части
    снимков и 1026x659 у другой) и передаёт его сюда.

    **Как пишется.** Через блок свойств в `createmodel` - тот же путь, что у
    `import_model_text`: `x_center`, `y_center`, `x_scale`, `y_scale` это
    свойства страницы. Объекты не добавляются, прежний скрипт страницы
    возвращается на место.

    Проверено живьём 04.10.2026: после записи `x_scale = 4` снимок той же
    модели стал другим (4473 байта до, 7923 после) - свойства кадра управляют
    отрисовкой.
    """
    project = session.ensure_project()
    page = project.get_main_page()
    frame = model_frame(page)
    if frame is None:
        raise ToolError(
            "На странице нет блоков с читаемыми габаритами \N{EM DASH} "
            "подгонять кадр "
            "не по чему: габариты читаются из `Points` и `get_size`."
        )
    left, top, right, bottom = frame
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    scale, view_x, view_y = fit_geometry(frame, canvas_w, canvas_h)

    from simintech_api.model_operations import build_import_model_text_body

    from .page_script import refuse_contour_failure, run_contour

    props = (
        "(\n"
        f"  x_center = {view_x:g},\n"
        f"  y_center = {view_y:g},\n"
        f"  x_scale = {scale:g},\n"
        f"  y_scale = {scale:g}\n"
        ")\n"
    )
    outcome, _restored = run_contour(
        build_import_model_text_body(props),
        failed="выставить кадр страницы не удалось")
    refuse_contour_failure(outcome, failed="подгонка кадра")
    return (
        f"Кадр выставлен по рамке модели: {width:.0f}x{height:.0f} px "
        f"на полотне {canvas_w:.0f}x{canvas_h:.0f}.\n"
        f"  центр рамки (координаты модели): ({cx:.1f}, {cy:.1f})\n"
        f"  записано: x_center = {view_x:.1f}, y_center = {view_y:.1f} "
        f"{EM_DASH} это смещение в пикселях, экран = модель * масштаб + "
        f"смещение\n"
        f"  масштаб: {scale:.3f}\n"
        f"Проверьте снимком `save_screenshot`: в кадр обязана попасть вся "
        f"модель целиком, без обрезки по краям."
    )
