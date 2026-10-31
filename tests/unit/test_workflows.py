"""Композиты: порядок вызовов и контракт частичного успеха."""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from simintech_mcp.specs import BlockSpec, ModelSpec, WireSpec
from simintech_mcp.tools import workflows

from _support import timing


def _patch(monkeypatch, calls, name, result):
    def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        return result
    monkeypatch.setattr(workflows, name, fake)


def test_build_model_spec_imports_blocks_and_connects(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)\n  kx_0 [Усилитель] (id=2)")
    _patch(monkeypatch, calls, "connect", "соединено")
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    _patch(monkeypatch, calls, "layout_place", "расставлено")
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(
            blocks=[
                BlockSpec(name="k_0", type="Константа", props={"a": 2}),
                BlockSpec(name="kx_0", type="Усилитель", props={"a": 3}),
            ],
            wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
        ),
        layout=True,
    )

    imported = [c for c in calls if c[0] == "import_model_text"]
    assert imported, calls
    assert 'type = "wire"' not in imported[0][1][0]
    names = [c[0] for c in calls]
    assert names.index("fit_port_blocks") < names.index("connect"), (
        f"подгонка размеров обязана идти до connect: {names}")
    connects = [c for c in calls if c[0] == "connect"]
    assert connects and connects[0][1][:2] == ("k_0", "kx_0")
    assert "Модель собрана" in text


def test_build_model_requires_exactly_one_source():
    with pytest.raises(ToolError):
        workflows.build_model()
    with pytest.raises(ToolError):
        workflows.build_model(spec=ModelSpec(), model_text="x")


def test_build_model_reports_partial_success(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)\n  kx_0 [Усилитель] (id=2)")
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")

    def fail(*args, **kwargs):
        raise ToolError("порт занят")

    monkeypatch.setattr(workflows, "connect", fail)
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(
            blocks=[
                BlockSpec(name="k_0", type="Константа"),
                BlockSpec(name="kx_0", type="Усилитель"),
            ],
            wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
        ),
        layout=False,
    )

    assert "WARNING" in text
    assert "порт занят" in text


def test_inspect_model_uses_only_read_tools(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)")
    _patch(monkeypatch, calls, "list_wires",
           "Линий связи: 0 (блоков на странице: 1).")

    text = workflows.inspect_model()

    assert [c[0] for c in calls] == ["list_blocks", "list_wires"]
    assert "k_0" in text


def test_simulate_runs_and_summarizes(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "set_calc_time", "время задано")
    _patch(monkeypatch, calls, "run", "расчёт завершён")
    _patch(monkeypatch, calls, "get_time", 1.0)
    _patch(monkeypatch, calls, "read_output_file", "0 1")
    _patch(monkeypatch, calls, "summarize_output_file", "min/max")

    text = workflows.simulate(
        end_time=2.0, to_time=1.0, output_path="out.txt")

    assert [c[0] for c in calls] == [
        "set_calc_time", "run", "get_time", "read_output_file",
        "summarize_output_file"]
    assert "Модельное время: 1" in text


def test_format_model_place_and_check(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    _patch(monkeypatch, calls, "layout_place", "расставлено")
    _patch(monkeypatch, calls, "check_model_layout", "проверено")
    _patch(monkeypatch, calls, "audit_routing", "аудит")

    text = workflows.format_model(mode="place", check=True)

    assert [c[0] for c in calls] == [
        "fit_port_blocks", "layout_place", "check_model_layout",
        "audit_routing"]
    assert "расставлено" in text


def test_format_model_rejects_hires_with_zoom():
    with pytest.raises(ToolError):
        workflows.format_model(hires=True, zoom=1.0)


def test_doctor_reports_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(workflows, "project_status",
                        lambda: "SimInTech подключён (PID=1, ownership=owned)")
    monkeypatch.setattr(workflows.sandbox, "safe_output_root",
                        lambda: str(tmp_path))
    monkeypatch.setattr(workflows.skills, "skills_root",
                        lambda: str(tmp_path))
    monkeypatch.setattr(workflows.session, "current_project", lambda: object())
    monkeypatch.setattr(workflows.session, "current_pack", lambda: None)

    text = workflows.doctor()

    assert "[OK] com" in text
    assert "[OK] output_dir" in text
    assert "[WARN] pack" in text


def test_build_model_explains_probe_not_running(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text",
           "Вердикт: скрипт отработал, но модель не считает")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)")
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(blocks=[BlockSpec(name="k_0", type="Константа")]),
        layout=False,
    )

    assert "simulate" in text
    assert "пробному контуру" in text


def test_format_model_explains_probe_not_running(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "layout_place", "нормализовано")
    _patch(monkeypatch, calls, "check_model_layout",
           "Контур: скрипт отработал, но модель не считает")
    _patch(monkeypatch, calls, "audit_routing", "аудит")

    text = workflows.format_model(mode="normalize", check=True)

    assert "simulate" in text
    assert "пробному контуру" in text


def test_build_model_refuses_unsaved_current_project(monkeypatch):
    monkeypatch.setattr(workflows.session, "current_project", lambda: object())
    monkeypatch.setattr(workflows.session, "unsaved_changes", lambda: True)

    with pytest.raises(ToolError, match="несохранённые"):
        workflows.build_model(
            spec=ModelSpec(blocks=[BlockSpec(name="k", type="Константа")]),
            new_project=True,
        )


def test_build_model_default_extends_current_project(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k [Константа] (id=1)")
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    monkeypatch.setattr(workflows.session, "current_project", lambda: object())
    monkeypatch.setattr(workflows.session, "unsaved_changes", lambda: False)

    text = workflows.build_model(
        spec=ModelSpec(blocks=[BlockSpec(name="k", type="Константа")]),
        layout=False,
    )

    assert "используется текущий проект" in text
    assert not [call for call in calls if call[0] == "create_project"]


def test_format_model_sizes_fits_and_normalizes(monkeypatch):
    """mode=sizes: подгонка и NormalizeWire без перемещения блоков."""
    calls = []
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    _patch(monkeypatch, calls, "layout_place", "нормализовано")

    text = workflows.format_model(mode="sizes")

    assert calls[0][0] == "fit_port_blocks"
    assert calls[1][0] == "layout_place"
    assert calls[1][2].get("normalize_only") is True
    assert "подогнано" in text and "нормализовано" in text


def test_format_model_normalize_does_not_fit(monkeypatch):
    """mode=normalize: чистая нормализация, подгонки нет."""
    calls = []
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    _patch(monkeypatch, calls, "layout_place", "нормализовано")

    workflows.format_model(mode="normalize")

    assert [c[0] for c in calls] == ["layout_place"]
    assert calls[0][2].get("normalize_only") is True


def test_format_model_place_warns_on_fit_failure(monkeypatch):
    """Сбой подгонки не отменяет раскладку и виден предупреждением."""
    calls = []
    _patch(monkeypatch, calls, "layout_place", "расставлено")

    def fail(*args, **kwargs):
        raise ToolError("контур не отработал")

    monkeypatch.setattr(workflows, "fit_port_blocks", fail)

    text = workflows.format_model(mode="place")

    assert "WARNING: подгонка размеров не выполнена" in text
    assert "расставлено" in text


def test_build_model_fit_failure_is_warning(monkeypatch):
    """Сбой подгонки при сборке не отменяет connect и раскладку."""
    calls = []
    _patch(monkeypatch, calls, "create_project", "проект создан")
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k_0 [Константа] (id=1)\n  kx_0 [Усилитель] (id=2)")
    _patch(monkeypatch, calls, "connect", "соединено")
    _patch(monkeypatch, calls, "layout_place", "расставлено")

    def fail(*args, **kwargs):
        raise ToolError("контур не отработал")

    monkeypatch.setattr(workflows, "fit_port_blocks", fail)
    monkeypatch.setattr(workflows.session, "current_project", lambda: None)

    text = workflows.build_model(
        spec=ModelSpec(
            blocks=[BlockSpec(name="k_0", type="Константа"),
                    BlockSpec(name="kx_0", type="Усилитель")],
            wires=[WireSpec(src="k_0:out:0", dst="kx_0:in:0")],
        ),
        layout=True,
    )

    assert "WARNING: подгонка размеров не выполнена" in text
    names = [c[0] for c in calls]
    assert "connect" in names and "layout_place" in names


def test_build_model_fit_sizes_false_skips_fit(monkeypatch):
    """fit_sizes=False: подгонка не вызывается (явный обход)."""
    calls = []
    _patch(monkeypatch, calls, "import_model_text", "блоки импортированы")
    _patch(monkeypatch, calls, "list_blocks",
           "Блоки:\n  k [Константа] (id=1)")
    _patch(monkeypatch, calls, "fit_port_blocks", "подогнано")
    monkeypatch.setattr(workflows.session, "current_project", lambda: object())

    workflows.build_model(
        spec=ModelSpec(blocks=[BlockSpec(name="k", type="Константа")]),
        layout=False, fit_sizes=False,
    )

    assert not [call for call in calls if call[0] == "fit_port_blocks"]


def test_simulate_reports_absolute_timing(monkeypatch):
    calls = []
    _patch(monkeypatch, calls, "run", "расчёт завершён")
    _patch(monkeypatch, calls, "get_time", 1.0)

    text = workflows.simulate(to_time=1.0)
    data = timing(text)

    assert data["t_start"] > 1_000_000_000_000
    assert data["elapsed_ms"] == data["t_end"] - data["t_start"]
