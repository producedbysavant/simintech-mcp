"""Тесты каталога свойств блоков (без COM)."""

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from simintech_api.catalog import (  # noqa: E402
    BlockCatalog,
    clean_value,
    decode_xprt,
    load_default_catalog,
    parse_xprt_block_props,
    parse_xprt_readonly,
)

# ─── Фикстуры .xprt ───────────────────────────────────────────────

# Основной формат: параметры блока в <custom_props>, значения в бэктиках
XPRT_BACKTICK = """<?xml version="1.0" encoding="utf-8"?>
<project>
  <object>
    <name>`Gain1`</name>
    <class_name>`Усилитель`</class_name>
    <visual_props>
      <data><name>`Color`</name><value>`16777215`</value></data>
    </visual_props>
    <custom_props>
      <data><name>`a`</name><mode>`1`</mode><value>`2.5`</value></data>
    </custom_props>
  </object>
  <object>
    <name>`Sum1`</name>
    <class_name>`Сумматор`</class_name>
    <visual_props>
      <data><name>`Points`</name><value>`[(30 , 20)]`</value></data>
    </visual_props>
    <custom_props>
      <data><name>`a`</name><mode>`1`</mode><value>`[1 , -1]`</value></data>
    </custom_props>
  </object>
</project>
"""

# Тот же смысл, но без бэктиков — парсер должен быть терпимым
XPRT_PLAIN = """<project>
  <object>
    <name>Gain1</name>
    <class_name>Усилитель</class_name>
    <custom_props>
      <data><name>a</name><value>2.5</value></data>
    </custom_props>
  </object>
</project>
"""

# Параметры лежат в custom_props, а не в visual_props: оформление игнорируется
XPRT_COLOR_IS_NOT_PARAM = """<project>
  <object>
    <name>`Gain1`</name>
    <class_name>`Усилитель`</class_name>
    <visual_props>
      <data><name>`Color`</name><value>`16777215`</value></data>
      <data><name>`Points`</name><value>`[(30 , 20),(60 , 20)]`</value></data>
      <data><name>`LabelFont`</name><value>`Cambria`</value></data>
    </visual_props>
    <custom_props>
      <data><name>`a`</name><value>`1`</value></data>
    </custom_props>
  </object>
</project>
"""

# Графические объекты параметров не несут — в каталог не попадают
XPRT_WITH_DECOR = """<project>
  <object>
    <name>`Line1`</name>
    <class_name>`Line`</class_name>
    <visual_props>
      <data><name>`Points`</name><value>`[(0,0),(10,10)]`</value></data>
    </visual_props>
  </object>
  <object>
    <name>`Text1`</name>
    <class_name>`RotatedText`</class_name>
    <visual_props>
      <data><name>`Text`</name><value>`подпись`</value></data>
    </visual_props>
  </object>
</project>
"""

# Блок без custom_props — параметров восстановить нельзя
XPRT_NO_CUSTOM = """<project>
  <object>
    <name>`X`</name>
    <class_name>`Интегратор`</class_name>
    <visual_props>
      <data><name>`Color`</name><value>`1`</value></data>
    </visual_props>
  </object>
</project>
"""

# Вычисляемый параметр (mode 0) не задаётся
XPRT_WITH_COMPUTED = """<project>
  <object>
    <name>`Gain1`</name>
    <class_name>`Усилитель`</class_name>
    <custom_props>
      <data><name>`a`</name><mode>`1`</mode><value>`1`</value></data>
      <data><name>`formula_visible`</name><mode>`0`</mode><value>`0`</value></data>
    </custom_props>
  </object>
</project>
"""


# ─── Разбор .xprt ─────────────────────────────────────────────────

def test_clean_value_strips_backticks():
    assert clean_value("`2.5`") == "2.5"
    assert clean_value("  `abc`  ") == "abc"
    assert clean_value(None) == ""
    assert clean_value("") == ""


def test_parse_backtick_format():
    """Основной формат .xprt: параметры в <custom_props>, значения в бэктиках."""
    parsed = parse_xprt_block_props(XPRT_BACKTICK)

    assert set(parsed) == {"Усилитель", "Сумматор"}
    assert parsed["Усилитель"]["a"] == "2.5"
    assert parsed["Сумматор"]["a"] == "[1 , -1]"


def test_parse_plain_format():
    """Формат без бэктиков разбирается тем же кодом."""
    parsed = parse_xprt_block_props(XPRT_PLAIN)

    assert parsed["Усилитель"]["a"] == "2.5"


def test_parse_ignores_visual_props():
    """Оформление из <visual_props> в параметры не попадает.

    Это ключевое различие: Color/Points/LabelFont лежат в <visual_props>
    и параметрами расчёта не являются.
    """
    parsed = parse_xprt_block_props(XPRT_COLOR_IS_NOT_PARAM)

    props = parsed["Усилитель"]
    assert props == {"a": "1"}
    for graphical in ("Color", "Points", "LabelFont"):
        assert graphical not in props


def test_parse_skips_decor_classes():
    """Графические объекты в каталог не попадают."""
    parsed = parse_xprt_block_props(XPRT_WITH_DECOR)

    assert parsed == {}


def test_parse_skips_objects_without_custom_props():
    """Без <custom_props> параметров нет — блок пропускается."""
    assert parse_xprt_block_props(XPRT_NO_CUSTOM) == {}


def test_parse_readonly_marks_computed_params():
    """mode 0 — вычисляемый параметр, mode 1 — задаваемый."""
    readonly = parse_xprt_readonly(XPRT_WITH_COMPUTED)

    assert readonly["Усилитель"] == ["formula_visible"]
    assert "a" not in readonly["Усилитель"]


def test_parse_empty_text():
    assert parse_xprt_block_props("") == {}
    assert parse_xprt_block_props("не xml вовсе") == {}
    assert parse_xprt_readonly("") == {}


def test_decode_xprt_handles_utf8_bom():
    """SimInTech пишет .xprt в UTF-8 с BOM, а не в cp1251.

    Чтение как cp1251 превращает русские имена классов в мусор — молча, без
    ошибки. Именно на этом ломалась генерация каталога.
    """
    text = "<?xml version=\"1.0\" encoding=\"utf-8\"?><class_name>`Усилитель`</class_name>"
    raw = text.encode("utf-8-sig")

    assert decode_xprt(raw) == text
    assert "Усилитель" in decode_xprt(raw)


def test_decode_xprt_falls_back_to_cp1251():
    """Старые версии с cp1251 читаются запасным путём."""
    haystack = "выдуманный текст, не UTF-8"
    raw = haystack.encode("cp1251") + b"\xff\xfe\xfd"

    assert "выдуманный" in decode_xprt(raw)


# ─── BlockCatalog ─────────────────────────────────────────────────

def test_catalog_roundtrip(tmp_path):
    """Сохранение и загрузка не теряют данные."""
    original = BlockCatalog(
        classes={"Усилитель": {"a": "1", "Name": ""}},
        meta={"source": "test"},
    )
    path = tmp_path / "catalog.json"
    original.save(path)

    loaded = BlockCatalog.load(path)

    assert loaded.classes() == ["Усилитель"]
    assert loaded.props_for("Усилитель") == ["Name", "a"]
    assert loaded.defaults_for("Усилитель") == {"a": "1", "Name": ""}
    assert loaded.meta["source"] == "test"


def test_catalog_load_missing_file_is_empty(tmp_path):
    """Отсутствующий файл — пустой каталог, а не исключение."""
    catalog = BlockCatalog.load(tmp_path / "нет.json")

    assert len(catalog) == 0
    assert catalog.props_for("Усилитель") == ["Name"]


def test_catalog_props_for_unknown_class_returns_common():
    """Для неизвестного класса доступны только общие свойства."""
    catalog = BlockCatalog(classes={"Усилитель": {"a": "1"}})

    assert catalog.has("Усилитель") is True
    assert catalog.has("НетТакого") is False
    assert catalog.props_for("НетТакого") == ["Name"]


def test_catalog_save_is_utf8_json(tmp_path):
    """Каталог пишется читаемым UTF-8 (русские имена классов)."""
    catalog = BlockCatalog(classes={"Константа": {"y0": "0"}})
    path = catalog.save(tmp_path / "c.json")

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert "Константа" in raw["classes"]
    assert path.read_text(encoding="utf-8").count("\\u") == 0


# ─── Конвейер генерации (поддельный COM) ──────────────────────────

def test_generate_catalog_pipeline(tmp_path, monkeypatch):
    """Создание блоков → .xprt → разбор → каталог.

    Клиент и проект поддельные: проверяется логика обхода классов и разбора
    XML, а не COM. Реальный прогон возможен только на Windows.
    """
    import simintech_api.core.project as project_module
    from simintech_api.catalog import generate_catalog

    source_xprt = tmp_path / "source.xprt"
    source_xprt.write_text(XPRT_BACKTICK, encoding="cp1251")

    created = []

    class FakeBlock:
        def __init__(self, class_name):
            self.class_name = class_name

        def set_name(self, name):
            created.append((self.class_name, name))
            return self

    class FakePage:
        def create_block(self, class_name, x, y):
            return FakeBlock(class_name)

    class FakeProject:
        id = 1

        def get_main_page(self):
            return FakePage()

        def save_xml(self, path):
            pathlib.Path(path).write_text(
                source_xprt.read_text(encoding="cp1251"), encoding="cp1251")

        def close(self):
            pass

    monkeypatch.setattr(
        project_module.Project, "new",
        classmethod(lambda cls, client: FakeProject()),
    )

    catalog = generate_catalog(client=object(),
                               classes=["Усилитель", "Сумматор"])

    assert created, "блоки не создавались"
    assert catalog.has("Усилитель")
    assert "a" in catalog.props_for("Усилитель")
    assert catalog.meta["source"] == "generated"
    assert catalog.meta["failed"] == []


def test_generate_catalog_records_failures(tmp_path, monkeypatch):
    """Класс, который не удалось создать, попадает в meta['failed']."""
    import simintech_api.core.project as project_module
    from simintech_api.catalog import generate_catalog

    source_xprt = tmp_path / "source.xprt"
    source_xprt.write_text(XPRT_BACKTICK, encoding="cp1251")

    class FakeBlock:
        def set_name(self, name):
            return self

    class FakePage:
        def create_block(self, class_name, x, y):
            if class_name == "Плохой":
                raise RuntimeError("CreateBlock не сработал")
            return FakeBlock()

    class FakeProject:
        id = 1

        def get_main_page(self):
            return FakePage()

        def save_xml(self, path):
            pathlib.Path(path).write_text(
                source_xprt.read_text(encoding="cp1251"), encoding="cp1251")

        def close(self):
            pass

    monkeypatch.setattr(
        project_module.Project, "new",
        classmethod(lambda cls, client: FakeProject()),
    )

    catalog = generate_catalog(client=object(),
                               classes=["Усилитель", "Плохой"])

    assert catalog.meta["failed"] == ["Плохой"]


# ─── Засеянный каталог ────────────────────────────────────────────

def test_default_catalog_loads_generated_data():
    """Каталог читается и содержит классы, сгенерированные из SimInTech."""
    catalog = load_default_catalog(reload=True)

    assert len(catalog) > 0
    for class_name in ("Константа", "Усилитель", "Сумматор", "Интегратор"):
        assert catalog.has(class_name), f"нет класса {class_name}"


def test_default_catalog_uses_real_property_names():
    """Имена параметров — фактические, а не читаемые из docs/ или примеров.

    Проверка-ограничитель на проверенные на реальном SimInTech данные
    (SimInTech64, 2026-09-10): у «Константы» параметр `a`, а НЕ `y0`;
    у «Сумматора» только `a`, а `xn` не существует. Запись в несуществующее
    имя не даёт ошибки, поэтому такая ошибка в каталоге была бы молчаливой.
    """
    catalog = load_default_catalog(reload=True)
    invented = {"value", "signs", "numInputs", "reset"}

    all_props = set()
    for class_name in catalog.classes():
        all_props.update(catalog.props_for(class_name))

    assert not (all_props & invented), (
        f"в каталоге выдуманные имена: {all_props & invented}"
    )
    assert "a" in catalog.props_for("Константа")
    assert "y0" not in catalog.props_for("Константа")
    assert "a" in catalog.props_for("Усилитель")
    assert "xn" not in catalog.props_for("Сумматор")


def test_default_catalog_is_generated_not_handwritten():
    """Каталог помечен как сгенерированный из реального SimInTech."""
    catalog = load_default_catalog(reload=True)

    assert catalog.meta.get("source") == "generated"
    # «failed» — список классов, которые CreateBlock не создал. Сейчас пуст:
    # не создаваемые классы перенесены в UNSUPPORTED_COM_BLOCK_CLASSES.
    assert catalog.meta.get("failed") == []


def test_default_catalog_records_computed_params():
    """Вычисляемые параметры (mode 0) отмечены как readonly."""
    catalog = load_default_catalog(reload=True)

    assert catalog.is_readonly("Усилитель", "formula_visible") is True
    assert catalog.is_readonly("Усилитель", "a") is False
    assert "formula_visible" in catalog.readonly_for("Усилитель")
