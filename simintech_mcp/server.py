"""FastMCP-сервер для управления SimInTech.

Оборачивает библиотеку simintech-api (COM API) в MCP-инструменты, чтобы
ИИ-агент мог создавать модели, соединять блоки, запускать расчёт и читать
сигналы через текстовые вызовы.

Требуется Windows с зарегистрированным COM-сервером (mmain.exe /regserver).

Запуск:
    simintech-mcp
    python -m simintech_mcp.server
"""

from __future__ import annotations

import sys
from typing import Optional

from fastmcp import FastMCP

from simintech_api import COMClient, Project

# ─── MCP-сервер ────────────────────────────────────────────────────

mcp = FastMCP(
    "simintech",
    instructions=(
        "Управление SimInTech через COM API. Создание моделей: "
        "create_project → add_block → connect → run → get_signal. "
        "Требуется Windows и mmain.exe /regserver."
    ),
)

# ─── Состояние сессии ─────────────────────────────────────────────

_client: Optional[COMClient] = None
_project: Optional[Project] = None


def _ensure_client() -> COMClient:
    """Подключиться к COM-серверу (лениво)."""
    global _client
    if _client is None or not _client.connected:
        _client = COMClient(silent_mode=True).connect()
    return _client


def _ensure_project() -> Project:
    if _project is None:
        raise ValueError("Нет открытого проекта. Сначала вызовите create_project")
    return _project


# ─── Подключение ──────────────────────────────────────────────────

@mcp.tool()
def status() -> str:
    """Проверить доступность COM-сервера SimInTech (Windows)."""
    if sys.platform != "win32":
        return "COM SimInTech доступен только на Windows"
    try:
        c = _ensure_client()
        pid = c.get_process_id()
        return f"SimInTech подключён (PID={pid})"
    except Exception as exc:
        return f"SimInTech недоступен: {exc}"


@mcp.tool()
def disconnect() -> str:
    """Отсоединиться от COM-сервера SimInTech (не закрывая приложение)."""
    global _client
    if _client:
        _client.disconnect()
        _client = None
        return "Отсоединено"
    return "Уже отсоединён"


# ─── Проекты ──────────────────────────────────────────────────────

@mcp.tool()
def create_project(name: str = "model") -> str:
    """Создать новый проект SimInTech."""
    global _project
    prj = Project.new(_ensure_client())
    _project = prj
    return f"Проект '{name}' создан (id={prj.id})"


@mcp.tool()
def open_project(path: str) -> str:
    """Открыть существующий проект SimInTech (.prt/.xprt)."""
    global _project
    prj = Project.open(_ensure_client(), path)
    _project = prj
    return f"Проект открыт (id={prj.id})"


@mcp.tool()
def save_project(path: str) -> str:
    """Сохранить текущий проект в XML (.xprt)."""
    _ensure_project().save_xml(path)
    return f"Проект сохранён: {path}"


@mcp.tool()
def close_project() -> str:
    """Закрыть текущий проект."""
    global _project
    if _project:
        _project.close()
        _project = None
        return "Проект закрыт"
    return "Нет открытого проекта"


# ─── Блоки и связи ────────────────────────────────────────────────

@mcp.tool()
def add_block(class_name: str, name: str = "",
              x: float = 0.0, y: float = 0.0,
              props: str = "") -> str:
    """Добавить блок на главную страницу проекта.

    Args:
        class_name: класс блока (русское имя, напр. 'Константа',
            'Усилитель', 'Сумматор', 'Интегратор', 'Синусоида',
            'Ступенька', 'Временной график').
        name: имя блока (опционально).
        x, y: координаты центра блока.
        props: свойства через запятую, напр. 'a=2, y0=5'.
    """
    page = _ensure_project().get_main_page()
    block = page.create_block(class_name, x, y)
    if name:
        block.set_name(name)
    if props:
        for pair in props.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                block.set_property(k.strip(), _parse_val(v.strip()))
    block_name = name or block.get_name()
    return f"Блок '{class_name}' создан (id={block.id}, name={block_name})"


@mcp.tool()
def connect(src: str, dst: str,
            out_index: int = 0, in_index: int = 0) -> str:
    """Соединить выход блока src с входом блока dst линией связи.

    Args:
        src: имя/алиас блока-источника.
        dst: имя/алиас блока-приёмника.
        out_index: номер выходного порта источника (0-based).
        in_index: номер входного порта приёмника (0-based).
    """
    page = _ensure_project().get_main_page()
    b1 = page.find_block(src)
    b2 = page.find_block(dst)
    if b1 is None:
        return f"ERROR: блок '{src}' не найден на странице"
    if b2 is None:
        return f"ERROR: блок '{dst}' не найден на странице"
    wire = b1.connect(b2, out_index=out_index, in_index=in_index)
    return f"Соединено {src} -> {dst} (wire={wire.id})"


@mcp.tool()
def list_blocks() -> str:
    """Вывести список блоков текущей страницы проекта."""
    blocks = _ensure_project().get_main_page().get_blocks()
    if not blocks:
        return "Блоков на странице нет"
    lines = []
    for b in blocks[:50]:
        try:
            nm = b.get_name()
        except Exception:
            nm = ""
        lines.append(f"  {nm or '(без имени)'} [{b.class_name}] (id={b.id})")
    more = f"\n  ... и ещё {len(blocks) - 50}" if len(blocks) > 50 else ""
    return "Блоки:\n" + "\n".join(lines) + more


# ─── Расчёт ───────────────────────────────────────────────────────

@mcp.tool()
def run(to_time: Optional[float] = None) -> str:
    """Запустить расчёт проекта (опционально до момента времени).

    Args:
        to_time: время окончания расчёта в секундах (если указано).
    """
    sim = _ensure_project().simulation()
    sim.start()
    if to_time is not None:
        sim.run_to(to_time)
        return f"Расчёт до {to_time} с завершён (время={sim.get_time():.3f})"
    sim.run()
    return "Расчёт запущен"


@mcp.tool()
def step(count: int = 1) -> str:
    """Выполнить указанное число шагов расчёта."""
    sim = _ensure_project().simulation()
    sim.start()
    for _ in range(count):
        sim.step()
    return f"Выполнено шагов: {count}"


@mcp.tool()
def stop() -> str:
    """Остановить расчёт."""
    _ensure_project().simulation().stop()
    return "Расчёт остановлен"


@mcp.tool()
def get_time() -> float:
    """Вернуть текущее модельное время проекта."""
    return _ensure_project().simulation().get_time()


# ─── Сигналы ──────────────────────────────────────────────────────

@mcp.tool()
def list_signals() -> str:
    """Вывести список сигналов проекта (внешние + имена блоков из XML)."""
    prj = _ensure_project()
    signals = prj.list_signals()
    if not signals:
        return "Сигналов нет (модель без внешних интерфейсов)"
    lines = [f"  {s.name}" for s in signals[:50]]
    more = f"\n  ... и ещё {len(signals) - 50}" if len(signals) > 50 else ""
    return "Сигналы:\n" + "\n".join(lines) + more


@mcp.tool()
def get_signal(name: str) -> str:
    """Прочитать значение сигнала по имени блока.

    Args:
        name: имя сигнала (совпадает с именем блока).
    """
    try:
        sig = _ensure_project().signal(name)
        value = sig.read()
        return f"{name} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
def set_signal(name: str, value: float) -> str:
    """Записать значение в сигнал (по имени блока).

    Args:
        name: имя сигнала.
        value: значение (float).
    """
    try:
        sig = _ensure_project().signal(name)
        sig.write(value)
        return f"{name} = {value}"
    except Exception as exc:
        return f"ERROR: {exc}"


# ─── Утилиты ──────────────────────────────────────────────────────

@mcp.tool()
def layout_place(block_ids: str, connections: str) -> str:
    """Автоматически расставить блоки без наложений (LayeredPlacer).

    Args:
        block_ids: список id блоков через запятую, напр. 'A,B,C'.
        connections: пары 'src->dst' через запятую, напр. 'A->B,B->C'.
    """
    from simintech_api.layout import LayeredPlacer
    ids = [b.strip() for b in block_ids.split(",") if b.strip()]
    links = []
    for pair in connections.split(","):
        pair = pair.strip()
        if "->" in pair:
            s, _, d = pair.partition("->")
            links.append((s.strip(), d.strip()))
    pos = LayeredPlacer().place(ids, links, sizes={k: (60.0, 40.0) for k in ids})
    return "\n".join(
        f"{bid}: ({cx:.1f}, {cy:.1f})" for bid, (cx, cy) in pos.items()
    )


@mcp.tool()
def help_text() -> str:
    """Справка: список доступных команд MCP-сервера."""
    return (
        "Команды:\n"
        "  status — проверить COM\n"
        "  create_project / open_project / save_project / close_project\n"
        "  add_block(класс, имя, x, y, props) — добавить блок\n"
        "  connect(src, dst) — соединить блоки\n"
        "  list_blocks / list_signals\n"
        "  run(to_time) / step(n) / stop / get_time\n"
        "  get_signal(имя) / set_signal(имя, значение)\n"
        "  layout_place(блоки, связи) — авто-расстановка\n"
    )


# ─── Внутреннее ───────────────────────────────────────────────────

def _parse_val(text: str):
    text = text.strip()
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return text


def main() -> None:
    """Точка входа MCP-сервера (stdio)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
