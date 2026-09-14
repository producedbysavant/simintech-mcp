"""База сигналов SimInTech (SDB): разбор XML-выгрузки.

База трёхуровневая: Категория → Группа → Сигнал. Полное имя сигнала
собирается как ``<группа>_<сигнал>``; для поиска индексируется и составной
ключ ``<категория>.<группа>.<имя>``.

Поддерживается два режима: разбор готового XML-экспорта (не требует SimInTech)
и выгрузка базы в XML макросом (требует CLI или COM).

Структура XML и экспорт макросом описаны в `automation/signal-db.md`.
Перенесено из репозитория `simintech-connector` (заархивирован 2026-09-10).
"""
from __future__ import annotations

import re

# defusedxml, а не stdlib: обычный xml.etree разворачивает DTD-сущности, из-за
# чего документ вида «billion laughs» съедает память, а внешние сущности дают
# XXE. Выгрузки SimInTech DOCTYPE не содержат, поэтому строгий режим их не ломает.
import defusedxml.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SDBSignalInfo:
    """Информация о сигнале в БД."""
    name: str
    caption: str
    category: str
    group: str
    data_type: int = 0
    mode: int = 0
    value: str = ""
    fconstant: str = "0"
    full_name: str = ""


@dataclass
class GroupInfo:
    """Информация о группе сигналов."""
    name: str
    category: str
    signals: list[SDBSignalInfo] = field(default_factory=list)


@dataclass
class CategoryInfo:
    """Информация о категории сигналов."""
    name: str
    template: str = ""
    groups: list[GroupInfo] = field(default_factory=list)


def _strip_backticks(text: Optional[str]) -> str:
    """Удалить обратные кавычки из строки."""
    if text is None:
        return ""
    return text.strip().strip("`")


class SignalDatabase:
    """База данных сигналов SimInTech, загруженная из XML-экспорта.

    Пример:
        >>> db = SignalDatabase.from_xml("signals.xml")
        >>> cats = db.list_categories()
        >>> signals = db.find_signal("M*")
    """

    def __init__(self):
        self.categories: list[CategoryInfo] = []
        self._signal_map: dict[str, SDBSignalInfo] = {}  # full_name -> SDBSignalInfo
        self._loaded: bool = False
        self._source: str = ""

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @classmethod
    def from_xml(cls, xml_path: str | Path) -> "SignalDatabase":
        """Загрузить БД из XML-файла, экспортированного через dbexporttoxml."""
        db = cls()
        db._source = str(xml_path)
        tree = ET.parse(str(xml_path))
        root = tree.getroot()

        database = root.find("database")
        if database is None:
            database = root

        def ft(elem, tag, default=""):
            """findtext с удалением обратных кавычек."""
            val = elem.findtext(tag, default)
            return _strip_backticks(val) if val else default

        for cat_elem in database.findall("category"):
            cat_name = ft(cat_elem, "name")
            cat_template = ft(cat_elem, "nametemplate")

            cat = CategoryInfo(name=cat_name, template=cat_template)

            for group_elem in cat_elem.findall("group"):
                group_name = ft(group_elem, "name")
                group = GroupInfo(name=group_name, category=cat_name)

                signals_elem = group_elem.find("signals")
                if signals_elem is not None:
                    for signal_elem in signals_elem.findall("data"):
                        sig_name = ft(signal_elem, "name")
                        sig = SDBSignalInfo(
                            name=sig_name,
                            caption=ft(signal_elem, "caption"),
                            category=cat_name,
                            group=group_name,
                            data_type=int(ft(signal_elem, "type", "0") or "0"),
                            mode=int(ft(signal_elem, "mode", "0") or "0"),
                            value=ft(signal_elem, "value"),
                            fconstant=ft(signal_elem, "fconstant", "0") or "0",
                            full_name=f"{group_name}_{sig_name}",
                        )
                        group.signals.append(sig)
                        db._signal_map[sig.full_name] = sig
                        db._signal_map[f"{cat_name}.{group_name}.{sig_name}"] = sig

                cat.groups.append(group)
            db.categories.append(cat)

        db._loaded = True
        return db

    def list_categories(self) -> list[dict]:
        """Список всех категорий."""
        return [
            {
                "name": c.name,
                "template": c.template,
                "group_count": len(c.groups),
                "signal_count": sum(len(g.signals) for g in c.groups),
            }
            for c in self.categories
        ]

    def list_groups(self, category: Optional[str] = None) -> list[dict]:
        """Список групп. Опционально — фильтр по категории."""
        result = []
        for cat in self.categories:
            if category and cat.name != category:
                continue
            for g in cat.groups:
                result.append({
                    "name": g.name,
                    "category": cat.name,
                    "signal_count": len(g.signals),
                })
        return result

    def list_signals(
        self,
        category: Optional[str] = None,
        group: Optional[str] = None,
    ) -> list[dict]:
        """Список сигналов с опциональной фильтрацией."""
        result = []
        for cat in self.categories:
            if category and cat.name != category:
                continue
            for g in cat.groups:
                if group and g.name != group:
                    continue
                for s in g.signals:
                    result.append({
                        "name": s.name,
                        "full_name": s.full_name,
                        "group": g.name,
                        "category": cat.name,
                        "caption": s.caption,
                        "type": s.data_type,
                        "value": s.value,
                    })
        return result

    def find_signal(self, pattern: str) -> list[dict]:
        """Поиск сигналов по имени/полному имени (поддерживает *).

        Безопасен к ReDoS: * конвертируется в [^.]*, а не в .*.
        """
        if len(pattern) > 200:
            pattern = pattern[:200]
        pattern = pattern.lower()
        # * -> [^.]* предотвращает catastrophic backtracking
        safe = "".join("[^.]*" if c == "*" else re.escape(c) for c in pattern)
        try:
            regex = re.compile(safe)
        except re.error:
            regex = re.compile(re.escape(pattern))

        seen: set[str] = set()
        result = []
        for full_name, sig in self._signal_map.items():
            if regex.search(full_name.lower()):
                if sig.full_name in seen:
                    continue
                seen.add(sig.full_name)
                result.append({
                    "name": sig.name,
                    "full_name": sig.full_name,
                    "group": sig.group,
                    "category": sig.category,
                    "caption": sig.caption,
                    "type": sig.data_type,
                    "value": sig.value,
                })
        return result

    def get_signal_info(self, full_name: str) -> Optional[dict]:
        """Получить детальную информацию о сигнале по полному имени."""
        sig = self._signal_map.get(full_name)
        if sig is None:
            # Try searching
            for fn, s in self._signal_map.items():
                if s.name == full_name or fn.endswith(f".{full_name}"):
                    sig = s
                    break
        if sig is None:
            return None

        return {
            "name": sig.name,
            "full_name": sig.full_name,
            "group": sig.group,
            "category": sig.category,
            "caption": sig.caption,
            "type": sig.data_type,
            "mode": sig.mode,
            "value": sig.value,
            "fconstant": sig.fconstant,
        }

    def get_signals_by_type(self, data_type: int) -> list[dict]:
        """Получить все сигналы определённого типа."""
        result = []
        for sig in self._signal_map.values():
            if sig.data_type == data_type:
                result.append({
                    "name": sig.name,
                    "full_name": sig.full_name,
                    "group": sig.group,
                    "category": sig.category,
                    "caption": sig.caption,
                    "type": sig.data_type,
                    "value": sig.value,
                })
        return result

    def get_statistics(self) -> dict:
        """Статистика по БД."""
        categories = len(self.categories)
        groups = sum(len(c.groups) for c in self.categories)
        # Уникальные сигналы через full_name (каждый сигнал хранится
        # под двумя ключами: group_name и category.group.name)
        unique: set[str] = set()
        types: dict[int, int] = {}
        for sig in self._signal_map.values():
            if sig.full_name not in unique:
                unique.add(sig.full_name)
                types[sig.data_type] = types.get(sig.data_type, 0) + 1
        signals = len(unique)

        return {
            "categories": categories,
            "groups": groups,
            "signals": signals,
            "types": types,
            "source": self._source,
        }


def _escape_macro_str(s: str) -> str:
    """Экранировать строку для вставки в макрос SimInTech.

    В макроязыке строки в двойных кавычках, кавычка внутри —
    удваивается (как в Pascal).
    """
    return s.replace('"', '""')


def export_db_via_macro(
    project_file: str,
    output_path: str,
    mmain_path: Optional[str] = None,
) -> str:
    """Экспортировать БД сигналов в XML через макрос SimInTech.

    Args:
        project_file: Путь к проекту (.prt) или пакету (.pak)
        output_path: Путь для сохранения XML
        mmain_path: Путь к mmain.exe (опционально)

    Returns:
        Путь к созданному XML-файлу
    """
    from .cli_adapter import CLIAdapter

    cli = CLIAdapter(mmain_path=mmain_path)

    macro = (
        f"initialization\n"
        f'    prjid = openproject("{_escape_macro_str(project_file)}", 0);\n'
        f'    initproject(prjid, 0);\n'
        f'    dbexporttoxml(prjid, "{_escape_macro_str(output_path)}");\n'
        f"    closeapp;\n"
        f"end;\n"
    )

    result = cli.run_macro(macro)
    if not result.success:
        raise RuntimeError(f"Ошибка экспорта БД: {result.message}")

    return output_path
