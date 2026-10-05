"""Сегментная геометрия аудита маршрутов: попадания, треки, пересечения.

Проверяются чистые функции — предсказание ортогонали и три счётчика
читаемости. Живые формы линий среда не отдаёт, поэтому проверка строится
на предсказании; его края (границы габаритов, T-стыки, узкие зазоры)
закрепляются здесь.
"""

from __future__ import annotations

from simintech_mcp.geometry import (
    STUB,
    WIRE_PITCH,
    channel_width,
    collinear_overlap,
    cut_sizes,
    predicted_polyline,
    proper_crossing,
    segment_hits_rect,
    segments_of,
)

RECT = (100.0, 100.0, 140.0, 120.0)          # лево, верх, право, низ


def test_segment_hits_rect_through_body():
    """Отрезок через тело блока — попадание; все три ориентации."""
    assert segment_hits_rect((80.0, 110.0), (160.0, 110.0), RECT)      # гориз.
    assert segment_hits_rect((120.0, 80.0), (120.0, 140.0), RECT)      # верт.
    assert segment_hits_rect((90.0, 90.0), (150.0, 130.0), RECT)       # диаг.


def test_segment_hits_rect_edge_and_touch_are_not_hits():
    """По границе и касанием — не попадание: порт-блоки стоят вплотную."""
    assert not segment_hits_rect((80.0, 100.0), (160.0, 100.0), RECT)  # по верху
    assert not segment_hits_rect((80.0, 90.0), (140.0, 90.0), RECT)    # выше
    assert not segment_hits_rect((80.0, 110.0), (100.0, 110.0), RECT)  # до левой
    assert not segment_hits_rect((140.0, 110.0), (160.0, 110.0), RECT)  # от правой


def test_collinear_overlap_same_track():
    """Длинное наложение на одном треке — да; стык углом — нет."""
    assert collinear_overlap((0.0, 50.0), (100.0, 50.0),
                             (60.0, 50.0), (160.0, 50.0))
    assert collinear_overlap((50.0, 0.0), (50.0, 100.0),
                             (50.0, 60.0), (50.0, 160.0))  # вертикаль
    # Крест (горизонталь × вертикаль) — не коллинеарное наложение.
    assert not collinear_overlap((0.0, 50.0), (100.0, 50.0),
                                 (50.0, 0.0), (50.0, 100.0))
    # Перекрытие 4 px — короче шага трека: стык, не колея.
    assert not collinear_overlap((0.0, 50.0), (100.0, 50.0),
                                 (96.0, 50.0), (160.0, 50.0))
    # Разные треки — не считаем.
    assert not collinear_overlap((0.0, 50.0), (100.0, 50.0),
                                 (60.0, 58.0), (160.0, 58.0))
    assert WIRE_PITCH == 8.0


def test_proper_crossing_ignores_touch_and_shared_end():
    """Внутреннее пересечение — да; T-стык и общий конец — нет."""
    assert proper_crossing((0.0, 50.0), (100.0, 50.0),
                           (50.0, 0.0), (50.0, 100.0))
    # Вертикаль кончается ровно на горизонтали (T-стык) — не пересечение.
    assert not proper_crossing((0.0, 50.0), (100.0, 50.0),
                               (50.0, 0.0), (50.0, 50.0))
    # Общий конец.
    assert not proper_crossing((0.0, 50.0), (100.0, 50.0),
                               (100.0, 50.0), (100.0, 100.0))
    # Параллельные — нет.
    assert not proper_crossing((0.0, 50.0), (100.0, 50.0),
                               (0.0, 58.0), (100.0, 58.0))


def test_segments_of_skips_duplicate_points():
    """Точки-дубли не дают нулевых сегментов."""
    assert segments_of([(0.0, 0.0), (0.0, 0.0), (10.0, 0.0)]) == \
        [((0.0, 0.0), (10.0, 0.0))]
    assert segments_of([(0.0, 0.0)]) == []


def test_predicted_polyline_aligned_is_straight():
    """Порты на одной горизонтали — линия одним отрезком."""
    assert predicted_polyline((0.0, 40.0), (200.0, 40.0), 100.0) == \
        [(0.0, 40.0), (200.0, 40.0)]


def test_predicted_polyline_bend_goes_through_channel():
    """Разные Y — вылет, канал, вертикаль, заход (форма эскиза ТЗ)."""
    line = predicted_polyline((0.0, 40.0), (200.0, 120.0), 100.0)

    assert line == [(0.0, 40.0), (STUB, 40.0), (100.0, 40.0),
                    (100.0, 120.0), (200.0, 120.0)]


def test_predicted_polyline_back_edge_is_not_guessed():
    """Обратная связь (приёмник левее) не предсказывается — None, не догадка."""
    assert predicted_polyline((200.0, 40.0), (100.0, 120.0), 150.0) is None
    assert predicted_polyline((200.0, 40.0), (200.0, 120.0), 250.0) is None


def test_channel_width_never_below_one_track():
    """Канал не схлопывается: даже при нуле связей в нём один трек."""
    assert channel_width(0) == STUB + WIRE_PITCH
    assert channel_width(3) == STUB + 3 * WIRE_PITCH


def test_cut_sizes_counts_nets_crossing_each_gap():
    """В зазор входят связи источник слева, приёмник справа."""
    nets = [(0, 2, False), (0, 1, False), (1, 2, False)]

    assert cut_sizes(nets, 2) == [2, 2]
    assert cut_sizes([(0, 1, False)], 3) == [1, 0, 0]


def test_cut_sizes_skips_horizontally_aligned_nets():
    """Выровненная в одну горизонталь связь трека не занимает."""
    assert cut_sizes([(0, 1, True), (0, 2, False)], 2) == [1, 1]
