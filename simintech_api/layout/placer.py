"""Алгоритм размещения блоков (Sugiyama-подобный layered layout).

Вход: граф связей «блок -> список блоков-приёмников» + размеры блоков.
Выход: словарь {block_id: (cx, cy)} — координаты центров без наложений.

Схема расположения — горизонтальная (слева направо): слой по X,
внутри слоя блоки по Y.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from ..constants import BLOCK_GAP, LAYER_GAP
from ..exceptions import LayoutError


class LayeredPlacer:
    """Размещение блоков слоями по направлению сигнала.

    Args:
        layer_gap: расстояние между слоями по X.
        block_gap: расстояние между блоками в слое по Y.
    """

    def __init__(self, layer_gap: float = LAYER_GAP,
                 block_gap: float = BLOCK_GAP):
        self.layer_gap = layer_gap
        self.block_gap = block_gap

    def place(
        self,
        block_ids: Iterable[int],
        connections: Iterable[Tuple[int, int]],
        sizes: Optional[Dict[int, Tuple[float, float]]] = None,
        origin: Tuple[float, float] = (0.0, 0.0),
    ) -> Dict[int, Tuple[float, float]]:
        """Вычислить координаты центров блоков.

        Args:
            block_ids: все id блоков.
            connections: пары (источник, приёмник) — направление сигнала.
            sizes: {block_id: (width, height)}; по умолчанию (60, 40).
            origin: координата центра первого блока (cx0, cy0).

        Returns:
            {block_id: (cx, cy)}.

        Raises:
            LayoutError: если граф содержит цикл без внешних источников
                (обратные связи разрешены — они просто не образуют новый слой).
        """
        block_ids = list(block_ids)
        if not block_ids:
            return {}

        sizes = sizes or {}
        # Индексы блоков
        idx = {bid: i for i, bid in enumerate(block_ids)}

        # Строим граф: для каждого блока множество источников (от кого зависит)
        incoming: Dict[int, set] = {bid: set() for bid in block_ids}
        outgoing: Dict[int, set] = {bid: set() for bid in block_ids}
        for src, dst in connections:
            if src in idx and dst in idx:
                incoming[dst].add(src)
                outgoing[src].add(dst)

        # 1) Ранжирование (слои по X)
        layers: List[List[int]] = []          # layer -> [block_id]
        layer_of: Dict[int, int] = {}
        placed: set = set()

        # Первый слой — блоки без входов. Если таких нет (чистый цикл /
        # обратная связь без внешнего источника), назначаем корнем блок
        # с минимальным числом входов и считаем его источником.
        frontier = [b for b in block_ids if not incoming[b]]
        if not frontier and block_ids:
            root = min(block_ids, key=lambda b: len(incoming[b]))
            frontier = [root]
            # Исключаем root из зависимостей остальных — обратная связь
            # от root сама по себе (root не входит в свой слой)
            for bid in block_ids:
                incoming[bid].discard(root)

        while frontier:
            layer: List[int] = []
            next_frontier: List[int] = []
            for bid in frontier:
                if bid in placed:
                    continue
                # Проверяем, что все источники уже размещены в более ранних слоях
                if not incoming[bid] or all(s in placed for s in incoming[bid]):
                    layer.append(bid)
                    placed.add(bid)
                    next_frontier.extend(outgoing[bid])
            if not layer:
                # Остались только блоки в циклах — разместим их в новый слой
                layer = [b for b in frontier if b not in placed]
                placed.update(layer)
            if not layer:
                break
            layers.append(layer)
            for bid in layer:
                layer_of[bid] = len(layers) - 1
            frontier = [b for b in next_frontier
                        if b not in placed and b not in (item for sub in layers
                                                          for item in sub)]

        # Если что-то не разместилось (например, изолированные в цикле) — довесок
        remaining = [b for b in block_ids if b not in placed]
        if remaining:
            layers.append(remaining)
            for bid in remaining:
                layer_of[bid] = len(layers) - 1

        # 2) Упорядочивание внутри слоя (медианная эвристика)
        for li, layer in enumerate(layers):
            if li == 0:
                continue
            # Для каждого блока слоя — медиана позиций источников в предыдущем слое
            medians: Dict[int, float] = {}
            prev_positions = {bid: pos for pos, bid in enumerate(layers[li - 1])}
            for bid in layer:
                srcs = [prev_positions[s] for s in incoming[bid] if s in prev_positions]
                medians[bid] = _median(srcs) if srcs else float(len(prev_positions))
            layers[li] = sorted(layer, key=lambda b: medians[b])

        # 3) Координаты центров
        result: Dict[int, Tuple[float, float]] = {}
        cx0, cy0 = origin
        total_width = 0.0
        # Ширина каждого слоя = сумма ширин + зазоры
        layer_widths: List[float] = []
        for layer in layers:
            w = sum(sizes.get(b, (60.0, 40.0))[0] for b in layer)
            w += self.block_gap * max(0, len(layer) - 1)
            layer_widths.append(w)

        # Общая высота компоновки для вертикального центрирования
        max_layer_height = max(
            (sum(sizes.get(b, (60.0, 40.0))[1] for b in layer)
             + self.block_gap * max(0, len(layer) - 1))
            for layer in layers
        ) if layers else 0.0

        for li, layer in enumerate(layers):
            # Вертикальное центрирование слоя относительно самой высокой колонки
            col_h = sum(sizes.get(b, (60.0, 40.0))[1] for b in layer)
            col_h += self.block_gap * max(0, len(layer) - 1)
            y_start = cy0 + (max_layer_height - col_h) / 2.0
            y = y_start
            for bid in layer:
                w, h = sizes.get(bid, (60.0, 40.0))
                result[bid] = (cx0 + li * self.layer_gap, y + h / 2.0)
                y += h + self.block_gap
            cx0 += 0  # X каждого слоя фиксирован: cx0 + li*layer_gap

        return result


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0
