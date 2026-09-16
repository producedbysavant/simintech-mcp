"""Инструмент расстановки блоков: координаты, размеры, выравнивание проводов.

Порядок операций существенен: переместить блоки → `RepaintEditor` →
`NormalizeWire`. Нормализация до перерисовки не видит новых координат портов,
и провода остаются диагональными.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from .. import runtime, session
from ..app import mcp


# ─── Утилиты ──────────────────────────────────────────────────────

@mcp.tool()
@runtime._com_threaded
def layout_place(block_ids: str, connections: str) -> str:
    """Расставить блоки по слоям без наложений — **с применением** координат.

    Координаты считает `LayeredPlacer`, и они тут же применяются к блокам
    (`set_center`). Раньше инструмент только возвращал координаты текстом, а
    блоки не двигал: агент получал подтверждение расстановки, которой не было.
    Позиция задаётся до расчёта — она влияет только на вид схемы.

    Размеры блоков не задаются: `set_center` сохраняет родной размер каждого
    блока (он задан правилами разработки SimInTech, и подменять его нельзя), а
    расстановка считается по фактическим габаритам из `get_size`.

    Здесь же трассируются линии, созданные `connect` в этой сессии: после
    сдвига блоков геометрия пересчитывается `NormalizeWire`, иначе провода
    остаются диагональными (по прямой между портами). Это единственное место,
    где трассировка возможна: до расстановки блоки лежат в (0,0) друг на
    друге, и маршрут получается в обход наложенных блоков. Линии, созданные
    не в этой сессии, недоступны: COM не умеет перечислять линии страницы.

    Args:
        block_ids: блоки через запятую — имена (`k_0`, `kx_0`, `ToFile_0`; их
            даёт `list_blocks`) или числовые id.
        connections: пары `src->dst` через запятую, напр. `k_0->kx_0`. Оба конца
            должны быть перечислены в `block_ids`.
    """
    from simintech_api.layout import LayeredPlacer

    project = session._ensure_project()
    page = project.get_main_page()
    available = {}
    for block in page.get_blocks():
        available[str(block.id)] = block
        try:
            available[block.get_name()] = block
        except Exception:
            continue

    tokens = [t.strip() for t in block_ids.split(",") if t.strip()]
    if not tokens:
        raise ToolError("Не указаны блоки: передайте block_ids через запятую")
    missing = [t for t in tokens if t not in available]
    if missing:
        raise ToolError(
            f"Блоки не найдены на странице: {', '.join(missing)}. "
            f"Актуальные имена и id даёт list_blocks."
        )

    links = []
    for pair in connections.split(","):
        pair = pair.strip()
        if "->" not in pair:
            continue
        src, _, dst = pair.partition("->")
        src, dst = src.strip(), dst.strip()
        unknown = [t for t in (src, dst) if t not in tokens]
        if unknown:
            raise ToolError(
                f"В connections упомянуты блоки вне block_ids: "
                f"{', '.join(unknown)} — расстановка невозможна"
            )
        links.append((src, dst))

    # Размеры берём у самих блоков, а не подставляем свои: размер задан
    # правилами разработки SimInTech, и `set_center` не должен его менять.
    sizes = {token: available[token].get_size() for token in tokens}
    positions = LayeredPlacer().place(tokens, links, sizes=sizes)

    centers = {token: positions[token] for token in tokens}
    for token in tokens:
        available[token].set_center(*centers[token])

    # Перерисовка ДО чтения портов: пока проект не перерисован, порты отдают
    # координаты блоков на прежних местах, и выравнивание посчиталось бы по
    # устаревшей геометрии (проверено на SimInTech64 2026-09-15: без этого
    # шага «Сумматор» уехал на тысячу пикселей вниз).
    project.repaint()

    # Выравнивание по вертикали: основной вход блока (in_index=0) ставим на
    # одну высоту с выходом источника. Иначе линия идёт с лишним изломом — у
    # «Сумматора» входы на четверти и трёх четвертях высоты, а выход
    # «Усилителя» посередине, и прямой участок не получается. Координаты
    # берём у самих портов, поэтому считаем по фактической геометрии.
    unaligned = []
    # Смещения портов относительно центров читаем один раз: дальше блоки
    # двигаются, а COM отдаёт координаты портов только после перерисовки —
    # повторное чтение вернуло бы устаревшие значения.
    offsets = {}
    for _wire, src_name, out_index, dst_name, in_index in session._WIRES:
        for name, index, is_output in ((src_name, out_index, True),
                                       (dst_name, in_index, False)):
            key = (name, index, is_output)
            if key in offsets or name not in centers:
                continue
            block = available.get(name)
            if block is None:
                continue
            try:
                port = (block.get_out_port(index) if is_output
                        else block.get_in_port(index))
                offsets[key] = port.get_coords()[1] - centers[name][1]
            except Exception as exc:                          # noqa: BLE001
                unaligned.append(f"{name}[{index}] ({type(exc).__name__})")

    shifted = False
    for _wire, src_name, out_index, dst_name, in_index in session._WIRES:
        if in_index != 0:
            continue
        out_key = (src_name, out_index, True)
        in_key = (dst_name, in_index, False)
        if out_key not in offsets or in_key not in offsets:
            continue
        dy = ((centers[src_name][1] + offsets[out_key])
              - (centers[dst_name][1] + offsets[in_key]))
        if abs(dy) < 0.5:
            continue
        cx, cy = centers[dst_name]
        centers[dst_name] = (cx, cy + dy)
        available[dst_name].set_center(cx, cy + dy)
        shifted = True

    applied = [f"  {token}: ({centers[token][0]:.1f}, {centers[token][1]:.1f})"
               for token in tokens]

    # Порядок обязателен и проверен на SimInTech64: перемещение блоков →
    # перерисовка → трассировка. Без перерисовки SimInTech прокладывает
    # провода по прежним прямоугольникам блоков (они ещё лежат в (0,0) друг на
    # друге) и оставляет в геометрии точки вида (-160,-1056), которые потом не
    # пересчитываются. Если блоки сдвинулись на выравнивании — перерисовка
    # нужна ещё раз, уже перед трассировкой.
    if shifted:
        project.repaint()
    for wire, _src, _out, _dst, _in in session._WIRES:
        wire.normalize()
    routes = (f"\nЛинии связи: нормализовано {len(session._WIRES)} — участки "
              f"ортогональные" if session._WIRES
              else "\nЛиний связи в этой сессии нет — трассировать нечего")
    if unaligned:
        routes += (f"\nВНИМАНИЕ: выровнять не удалось для {len(unaligned)} "
                   f"связей: {', '.join(unaligned)}")
    return f"Расставлено блоков: {len(applied)}\n" + "\n".join(applied) + routes
