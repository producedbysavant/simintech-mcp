"""Просмотр субмодели: содержимое страницы из снимка IR или из файла.

Внутренняя субмодель отдаётся из снимка IR без COM: её страница уже
разобрана в `objects`/`wires` контейнера. Внешняя библиотечная субмодель
(`filename` вида `ecd\\ist\\...prt`) в проекте имеет только интерфейс:
страница лежит в файле поставки. Такой файл открывается средой как
отдельный проект, снимается `SaveProjectXML`, разбирается IR, и проект
закрывается; текущий проект сессии не меняется, его текущая страница
возвращается. `.xprt` разбирается напрямую, без COM.

Живой замер 30.10.2026: `C:\\SimInTech64\\bin\\include_mvtu\\ecd\\ist\\
ecd2_istochnik_napryazh.prt` открывается как проект (6 объектов, 5
проводов), а сам `.prt` - бинарный zlib-поток, не XML: без среды его
содержимое не прочитать.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from fastmcp.exceptions import ToolError
from simintech_api import Project
from simintech_api.model_ir import (
    Container,
    ModelIR,
    ModelObject,
    ObjectKind,
    WireEndKind,
    parse_xprt_file,
)

from .. import model_store, runtime, session
from ..app import mcp

#: Предел объектов и проводов в ответе: тот же, что у `query_model`.
MAX_ITEMS = 200

#: Каталоги поставки с библиотечными субмоделями (относительно корня).
_LIBRARY_SUBDIRS = ("include_mvtu", "ParamSet_mvtu")

#: Корни установки: `SIMINTECH_PATH` авторитетен, затем пути по умолчанию.
_DEFAULT_ROOTS = ("C:\\SimInTech64", "/mnt/c/SimInTech64")


def _split_filename(filename: str) -> Optional[List[str]]:
    """Части относительного пути или None, если путь небезопасен."""
    text = filename.replace("\\", "/")
    if text.startswith(("/", "\\")) or (len(text) > 1 and text[1] == ":"):
        return None
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return None
    return parts


def resolve_library_file(filename: str) -> str:
    """Найти файл библиотечной субмодели в каталогах поставки.

    Ищется `<корень>/bin/include_mvtu/<filename>` и
    `<корень>/bin/ParamSet_mvtu/<filename>`. Корень берётся из
    `SIMINTECH_PATH`, иначе - пути по умолчанию (Windows и WSL).

    Абсолютный путь и `..` отвергаются: `filename` приходит из модели,
    и выходить за пределы поставки он не должен.
    """
    parts = _split_filename(filename)
    if parts is None:
        raise ToolError(
            f"filename субмодели {filename!r} не относительный или содержит "
            f"`..`: искать вне каталогов поставки нельзя.")
    tried: List[str] = []
    roots: List[str] = []
    env = os.environ.get("SIMINTECH_PATH")
    if env:
        roots.append(env)
    roots.extend(_DEFAULT_ROOTS)
    for root in roots:
        for sub in _LIBRARY_SUBDIRS:
            candidate = os.path.join(root, "bin", sub, *parts)
            tried.append(candidate)
            if os.path.isfile(candidate):
                return candidate
    raise ToolError(
        f"файл субмодели {filename!r} не найден в поставке SimInTech; "
        f"проверены пути: {', '.join(tried)}")


def _wire_end(model: ModelIR, end: Any) -> str:
    """Конец провода как адрес `объект:порт` (или вид обрыва)."""
    if end.kind is WireEndKind.PORT:
        obj = next((item for item in model.objects
                    if item.id == end.object_id), None)
        name = obj.address if obj is not None else end.object_name
        return f"{name}:{end.port_index}"
    if end.kind is WireEndKind.BRANCH:
        return f"branch:{end.object_name}"
    return end.kind.value


def _object_item(obj: ModelObject) -> Dict[str, Any]:
    return {
        "address": obj.address,
        "name": obj.name,
        "class_name": obj.class_name,
        "kind": obj.kind.value,
        "ports": [
            {"index": port.index, "name": port.name,
             "direction": port.direction.value}
            for port in obj.ports
        ],
    }


def _view(model: ModelIR, container: Container, limit: int) -> Dict[str, Any]:
    """Содержимое контейнера: объекты, провода, границы вложенных субмоделей."""
    objects = [item for item in model.objects
               if item.container_id == container.id]
    by_id = {item.id: item for item in objects}
    wires = [item for item in model.wires
             if item.container_id == container.id]
    boundaries = [
        {
            "external_port": item.external_port_name,
            "internal": (by_id[item.internal_object_id].address
                         if item.internal_object_id in by_id else ""),
            "resolved": item.resolved,
            "reason": item.reason,
        }
        for item in model.boundaries
        if item.submodel_object_id in by_id
    ]
    truncated = len(objects) > limit or len(wires) > limit
    return {
        "container": container.address,
        "objects": [_object_item(item) for item in objects[:limit]],
        "wires": [
            {
                "address": item.address,
                "kind": item.kind.value,
                "from": _wire_end(model, item.start),
                "to": _wire_end(model, item.end),
            }
            for item in wires[:limit]
        ],
        "boundaries": boundaries[:limit],
        "coverage": {
            "containers": len(model.containers),
            "objects": len(model.objects),
            "wires": len(model.wires),
            "boundaries": len(model.boundaries),
            "external_submodels": model.coverage.external_submodels,
        },
        "discrepancies": {
            "total": len(model.discrepancies),
            "by_code": _discrepancy_codes(model),
        },
        "truncated": truncated,
        "limit": limit,
    }


def _discrepancy_codes(model: ModelIR) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in model.discrepancies:
        counts[item.code] = counts.get(item.code, 0) + 1
    return counts


def _root_container(model: ModelIR) -> Container:
    """Главная страница проекта: контейнер без родителя."""
    for item in model.containers:
        if not item.parent_object_id:
            return item
    raise ToolError("в разобранной модели нет главной страницы")


def _read_prt(path: str) -> Tuple[ModelIR, List[str]]:
    """Открыть `.prt` средой, снять XML и вернуть IR.

    Среда держит один проект за раз: `OpenProject` заменяет текущий, и
    после чтения прежний проект переоткрывается из своего файла (id
    меняется, снимок IR устаревает). Поэтому:

    * при несохранённых правках инструмент отказывает **до** COM -
      переоткрытие отбросило бы их;
    * проект, созданный из шаблона, не имеет файла для переоткрытия -
      отказ до COM;
    * если среда оставила прежний проект живым, он не переоткрывается:
      возвращается его активная страница (`SetCurrentPage`).

    Оба исхода названы в `warnings`: о потере снимка IR нельзя молчать.
    """
    previous = session.current_project()
    previous_path = session.opened_from()
    previous_page: Optional[Any] = None
    if previous is not None:
        if session.unsaved_changes():
            raise ToolError(
                "в текущем проекте есть несохранённые правки: просмотр "
                "`.prt` открывает другой проект, и правки были бы "
                "отброшены. Сохраните проект (`save_project`) или "
                "откатите их (`reload_project`) и повторите.")
        if not previous_path:
            raise ToolError(
                "текущий проект создан из шаблона и не имеет файла: "
                "после просмотра `.prt` вернуть его было бы нечем. "
                "Сохраните проект (`save_project`) и откройте из файла.")
        try:
            previous_page = previous.get_current_page()
        except Exception:                                      # noqa: BLE001
            previous_page = None
    client = session.ensure_client()
    root = tempfile.mkdtemp(prefix="simintech-submodel-")
    xml = os.path.join(root, "submodel.xprt")
    opened: Optional[Project] = None
    warnings: List[str] = []
    try:
        opened = Project.open(client, path)
        opened.save_xml(xml)
        model = parse_xprt_file(xml)
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if opened is not None:
            try:
                opened.close()
            except Exception as exc:                           # noqa: BLE001
                warnings.append(
                    f"открытый для просмотра проект закрыть не удалось "
                    f"({type(exc).__name__}: {exc})")
        if previous is not None:
            if _project_alive(previous):
                if previous_page is not None and previous_page.id:
                    try:
                        previous_page.activate()
                    except Exception as exc:                   # noqa: BLE001
                        warnings.append(
                            f"вернуть страницу текущего проекта не удалось "
                            f"({type(exc).__name__}: {exc})")
            elif previous_path:
                try:
                    restored = Project.open(client, previous_path)
                except Exception as exc:                       # noqa: BLE001
                    session.set_project(None)
                    warnings.append(
                        f"текущий проект закрыт средой и восстановить его "
                        f"из «{os.path.basename(previous_path)}» не удалось "
                        f"({type(exc).__name__}: {exc}); откройте проект "
                        f"заново (`open_project`), снимок IR недействителен")
                else:
                    session.set_project(restored, previous_path)
                    warnings.append(
                        f"текущий проект переоткрыт из "
                        f"«{os.path.basename(previous_path)}»: id сменился, "
                        f"снимок IR устарел - загрузите модель снова "
                        f"(`load_model`)")
    return model, warnings


def _project_alive(project: Project) -> bool:
    """Отвечает ли проект среды после открытия другого файла.

    `GetCurentPage` - самый дешёвый вызов проекта: закрытый проект
    отвечает ошибкой COM, живой - id страницы (0, если страницы нет).
    """
    try:
        project.get_current_page()
    except Exception:                                          # noqa: BLE001
        return False
    return True


def _read_file(path: str) -> Tuple[ModelIR, List[str]]:
    """Прочитать субмодель из файла: `.xprt` напрямую, `.prt` средой."""
    if not os.path.isabs(path):
        raise ToolError(
            f"путь «{path}» не абсолютный: передайте полный путь (как у "
            f"`open_project`).")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ToolError(f"файла «{path}» нет")
    suffix = os.path.splitext(path)[1].lower()
    if suffix in (".xprt", ".xml"):
        return parse_xprt_file(path), []
    if suffix == ".prt":
        return _read_prt(path)
    raise ToolError(
        f"файл «{os.path.basename(path)}»: ожидается .prt, .xprt или .xml")


def _ports_item(obj: ModelObject) -> List[Dict[str, Any]]:
    return [
        {"index": port.index, "name": port.name,
         "direction": port.direction.value}
        for port in obj.ports
    ]


@mcp.tool(
    title="Содержимое субмодели",
    annotations={
        "read_only_hint": False,
        "destructive_hint": False,
        "idempotent_hint": True,
        "open_world_hint": False,
    },
)
@runtime.com_threaded
def inspect_submodel(model_ref: str = "", address: str = "",
                     path: str = "", limit: int = 100) -> str:
    """Показать содержимое субмодели: объекты, порты, провода, порт-блоки.

    Два режима:

    * `model_ref` + `address` - субмодель модели, загруженной в IR.
      Внутренняя отдаётся из снимка без COM; внешняя библиотечная
      (`filename` из модели) находится в каталогах поставки SimInTech,
      открывается средой как отдельный проект и закрывается: текущий
      проект сессии не меняется.
    * `path` - файл `.xprt`/`.xml` разбирается напрямую, `.prt`
      открывается средой (`.prt` - бинарный zlib-поток, не XML).

    Ответ - JSON: `source` (`snapshot`/`library`/`file`), внешние порты,
    объекты страницы с портами, провода (`from`/`to`), границы вложенных
    субмоделей, покрытие и расхождения. XML и скрипты не возвращаются.
    """
    limit = max(1, min(int(limit), MAX_ITEMS))
    if not path and not (model_ref and address):
        raise ToolError(
            "нужен либо `path` (файл субмодели), либо `model_ref` + "
            "`address` (субмодель снимка)")
    payload: Dict[str, Any]
    warnings: List[str] = []
    if path:
        model, warnings = _read_file(path)
        payload = {
            "source": "file",
            "address": "",
            "class_name": "",
            "filename": os.path.basename(path),
            "file_path": os.path.abspath(path),
            "ports": [],
        }
        payload.update(_view(model, _root_container(model), limit))
    else:
        snapshot = model_store.get(model_ref)
        model_store.require_fresh(snapshot)
        obj = snapshot.model.object_by_address(address)
        if obj is None:
            raise ToolError(
                f"объекта «{address}» нет в снимке; адрес должен быть "
                f"полным, как в `find_objects`")
        if obj.kind is not ObjectKind.SUBMODEL:
            raise ToolError(
                f"«{address}» - не субмодель (вид {obj.kind.value}); "
                f"для блока используйте `query_model(inspect_node)`")
        container = next(
            (item for item in snapshot.model.containers
             if item.parent_object_id == obj.id), None)
        payload = {
            "address": obj.address,
            "class_name": obj.class_name,
            "filename": obj.filename,
            "ports": _ports_item(obj),
        }
        if container is not None:
            payload["source"] = "snapshot"
            payload["file_path"] = ""
            payload.update(_view(snapshot.model, container, limit))
        else:
            if not obj.filename:
                raise ToolError(
                    f"у субмодели «{address}» нет внутренней страницы и "
                    f"не назван файл: смотреть нечего")
            file_path = resolve_library_file(obj.filename)
            model, warnings = _read_file(file_path)
            payload["source"] = "library"
            payload["file_path"] = file_path
            payload.update(_view(model, _root_container(model), limit))
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False)
