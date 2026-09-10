---
name: simintech-library-curation
description: Use when block property names need to be known, verified, or added to the catalog — before writing a block catalog by hand, or when set_block_param reports a parameter missing from the catalog.
---

# Каталог свойств блоков

## Зачем каталог существует

В COM API **нет метода перечисления свойств блока**: ни `GetPropCount`, ни
`GetPropName`. Есть только:

- `GetBlockPropAsString(BlockId, PropName)` — прочитать
- `SetBlockProp(BlockId, PropName, StrValue)` — записать
- `GetPropHandle(BlockId, PropName)` — handle

Всем трём имя свойства нужно **знать заранее**. Поэтому имена хранятся в
каталоге: `simintech_api/data/block_catalog.json`.

## Почему нельзя писать каталог вручную

`SetBlockProp` **не отвергает неизвестное имя свойства**. Опечатка или
выдуманное имя не даёт ошибки: параметр «устанавливается», вызов успешен, а
расчёт идёт по прежнему значению. **Отказ молчаливый.**

Прямое подтверждение — карта COM API (`com_api_inventory.md`), раздел
«Критические ограничения»: `SetBlockProp("a")` не влияет на симуляцию, если
блок уже инициализирован.

Отсюда правило: **каталог генерировать, а не сочинять.**

## Как сгенерировать (Windows)

```bash
# требуется Windows и зарегистрированный COM-сервер
mmain.exe /regserver
python scripts/generate_block_catalog.py
```

Скрипт создаёт по одному блоку каждого класса из `SUPPORTED_COM_BLOCK_CLASSES`,
экспортирует проект в `.xprt` и разбирает секцию **`<custom_props>`** каждого
объекта. Перезаписывает `simintech_api/data/block_catalog.json`.

Два места, где легко ошибиться (обе ошибки уже были допущены):

- **`<custom_props>`, а не `<visual_props>`.** В `visual_props` лежит
  оформление (`Color`, `Points`, `LabelFont`); параметров расчёта там нет.
  Парсер, читавший `visual_props`, давал пустой каталог без ошибок.
- **UTF-8 с BOM, а не cp1251.** cp1251 декодирует любые байты, поэтому
  неверная кодировка не падает, а портит русские имена классов — и фильтр
  молча отбрасывает все классы.

Проверить результат:

```bash
python -m pytest tests/unit/test_catalog.py -v
```

Тест `test_default_catalog_uses_real_property_names` — ограничитель: он упадёт,
если в каталог попадут выдуманные имена (`value`, `signs`, `numInputs`).

## Текущее состояние каталога

Сгенерирован из реального SimInTech (`meta.source == "generated"`, SimInTech64,
2026-09-10): 12 классов. Не создались «Выход данных состояния» и «Состояние
автомата» — они значатся в `SUPPORTED_COM_BLOCK_CLASSES`, но `CreateBlock` их
не создаёт.

**Не копируйте имена из примеров.** Первая версия каталога была засеяна вручную
по `examples/` — и оказалась неверной: там ставили `y0` «Константе» (реальный
параметр `a`) и `xn` «Сумматору» (параметра нет). Ошибка была молчаливой:
модель строилась с дефолтной константой вместо заданной.

Проверить состояние:

```python
from simintech_api.catalog import load_default_catalog
cat = load_default_catalog()
print(cat.meta["source"])      # "seed" или "generated"
print(cat.classes())
```

## Ловушка: посторонняя документация

`docs/simintech-language/blocks/` содержит таблицы параметров блоков с
**читаемыми** именами: `value`, `signs`, `numInputs`, `num`, `den`, `reset`.
Реальные имена — короткие: `y0`, `a`, `xn`, `k`, `yk`. Они **не совпадают**.

Эта документация писалась из общих знаний теории управления, а не из
фактических имён свойств SimInTech. **Не используйте её как источник имён.**

## Если каталог неполон

Для класса вне каталога `get_block_params` вернёт «параметры неизвестны»,
а `set_block_param` — предупреждение «отсутствует в каталоге для класса».
Предупреждение означает: значение применено, но имя не проверено. Убедитесь,
что параметр реально влияет на модель, — либо отрегенерируйте каталог.
