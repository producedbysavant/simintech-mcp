
> **Для разработчиков тулкита.** Этот файл содержит внутреннюю техническую информацию и не требуется для повседневного использования тулкита.

# COM API SimInTech: полный справочник методов IMVTU_Server

Источник: `mmain.hpp` (MIDL), `SIT COM DEMO.cpp`, эксплуатация через comtypes.
CLSID: `{ACE730D7-1712-4C70-87C8-7E4C55622E91}`
IID: `{145848B3-2BE8-4497-9A6B-8A42DA658844}`

Статусы: ✅ проверено на практике, ➖ проверено частично, ❓ не проверено.

---

## 1. Управление проектами

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetProjectCount` | `() → long` | ✅ | Количество открытых проектов |
| `GetProjectIdByNumber` | `(long PrjNumber) → __int64` | ✅ | ID проекта по индексу (0-based) |
| `GetProjectIdByFileName` | `(BSTR PrjFileName) → __int64` | ✅ | ID проекта по имени файла |
| `OpenProject` | `(BSTR PrjFileName) → __int64` | ✅ | Открыть .prt/.xprt файл |
| `CloseProject` | `(__int64 ProjectId)` | ✅ | Закрыть проект |
| `GetActiveProject` | `() → __int64` | ✅ | ID активного проекта |
| `NewProject` | `() → __int64` | ✅ | Создать новый пустой проект |
| `OpenTemplate` | `(BSTR TemplateName) → __int64` | ✅ | Открыть шаблон |
| `GetOpenedFileName` | `(__int64 ProjectId) → BSTR` | ✅ | Путь к открытому файлу проекта |

---

## 2. Управление симуляцией (ядро тест-раннера)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `ProjectStart` | `(__int64 ProjectId)` | ✅ | Активировать солвер (обязательно перед Run/Step) |
| `ProjectRun` | `(__int64 ProjectId)` | ✅ | Запустить симуляцию (неблокирующий) |
| `ProjectStop` | `(__int64 ProjectId)` | ✅ | Остановить симуляцию |
| `ProjectPause` | `(__int64 ProjectId)` | ✅ | Поставить на паузу |
| `ProjectStep` | `(__int64 ProjectId)` | ✅ | Один шаг (~0.01 ед. времени) |
| `RunTo` | `(__int64 ProjectId, double TargetTime) → __int64` | ✅ | Запустить до target time (блокирующий) |
| `WaitForTime` | `(__int64 ProjectId, double TargetTime) → __int64` | ➖ | Ожидать target time во время Run |
| `GetProjectTime` | `(__int64 ProjectId) → double` | ✅ | Текущее модельное время |

**Порядок:** `ProjectStart` → `ProjectRun` (или `RunTo`, или `ProjectStep`) → `ProjectStop`
**RunTo vs ProjectRun:** RunTo блокирует выполнение до target_time; ProjectRun неблокирующий - требует poll `GetProjectTime`.

---

## 3. Доступ к сигналам (основной способ обмена данными)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `FindSignalData` | `(BSTR SignalName, __int64 ProjectId) → TDataDescriptor` | ✅ | Найти сигнал по имени (внешние/внутренние) |
| `FindProjectData` | `(BSTR DataName, __int64 ProjectId, long AccesForWrite) → TDataDescriptor` | ➖ | Поиск данных проекта (сложные имена, см. тесты) |
| `ReadAsFloat` | `(TDataDescriptor) → double` | ✅ | Чтение double-значения сигнала |
| `ReadAsInteger` | `(TDataDescriptor) → __int64` | ➖ | Чтение целого значения |
| `ReadAsString` | `(TDataDescriptor) → BSTR` | ➖ | Чтение строкового значения |
| `WriteAsFloat` | `(TDataDescriptor, double)` | ✅ | Запись double-значения сигнала |
| `WriteAsInteger` | `(TDataDescriptor, __int64)` | ➖ | Запись целого значения |
| `WriteAsString` | `(TDataDescriptor, BSTR)` | ➖ | Запись строки |

### Работа с массивами

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetArrayCount` | `(TDataDescriptor) → long` | ✅ | Размер массива |
| `GetExtArrayElement` | `(TDataDescriptor, long Index) → double` | ✅ | Чтение элемента double-массива |
| `GetIntArrayElement` | `(TDataDescriptor, long Index) → __int64` | ➖ | Чтение элемента int-массива |
| `SetExtArrayElement` | `(TDataDescriptor, long Index, double)` | ➖ | Запись элемента double-массива (нужен desc) |
| `SetIntArrayElement` | `(TDataDescriptor, long Index, __int64)` | ➖ | Запись элемента int-массива (нужен desc) |
| `SetArrayCount` | `(TDataDescriptor, long Count)` | ➖ | Изменить размер массива (нужен desc) |

### Типы данных сигналов (DataType в TDataDescriptor)

| Код | Тип | Описание |
|-----|-----|----------|
| 0 | double | Вещественное число |
| 1 | integer | Целое 64-bit |
| 2 | boolean | Логическое |
| 4 | string | Строка |
| 5 | array | Массив double (out_0..out_14) |
| 12 | intarray | Массив integer |

**TDataDescriptor** - структура `{ __int64 DataId; long DataType; }` с UUID `9A591BFF-874A-4801-9C62-4B91C4C098F5` (VT_RECORD). Требует **comtypes** для корректного marshalling - pywin32 ломает VT_RECORD.

---

## 4. Списки сигналов (обнаружение всех сигналов проекта)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetProjectSignalList` | `(__int64 ProjectId) → SAFEARRAY(__int64)` | ⚠️ | Список сигналов проекта. **Не работает под Wine** (SAFEARRAY не маршалится). Workaround: XPRT-парсер. |
| `GetPackSignalList` | `(__int64 PackId) → __int64` | ✅ | Список сигналов пака |
| `GetListCount` | `(__int64 ListId) → __int64` | ➖ | Количество элементов в списке |
| `GetDataInfoFromList` | `(__int64 ListId, long ElementNumber) → (name, caption, desc)` | ➖ | Информация об элементе |
| `FindDataInListByName` | `(__int64 ListId, BSTR Name) → long` | ➖ | Поиск индекса по имени |

**Примечание:** `GetProjectSignalList` требует comtypes (с pywin32 не работают вызовы с возвратом __int64).

---

## 5. Управление блоками

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `CreateBlock` | `(__int64 ProjectId, long LayerNo, __int64 ParentBlock, BSTR LibRecordName) → __int64` | ✅ | Создать блок по имени класса |
| `SetBlockPosition` | `(__int64 BlockId, double Left, double Top, double W, double H, double Angle)` | ➖ | Позиционирование блока |
| `SetBlockProp` | `(__int64 BlockId, BSTR PropName, BSTR StrValue)` | ✅ | Установка свойства (все значения - строкой!) |
| `GetBlockPropAsString` | `(__int64 BlockId, BSTR PropName) → BSTR` | ✅ | Чтение свойства блока |
| `GetBlockPluginName` | `(__int64 BlockId) → BSTR` | ➖ | Имя плагина блока (сигнатура: нужен BlockId) |
| `GetBlockCalcTemplate` | `(__int64 BlockId) → BSTR` | ➖ | Шаблон расчёта (сигнатура: нужен BlockId) |
| `SetGraphBlockProp` | `(__int64 BlockId, BSTR PropName, BSTR StrValue)` | ✅ | Установка графического свойства |
| `GetPropHandle` | `(__int64 BlockId, BSTR PropName) → __int64` | ✅ | Handle свойства |
| `GetGraphPropHandle` | `(__int64 BlockId, BSTR PropName) → __int64` | ✅ | Handle графического свойства |
| `GetBlockEngine` | `(__int64 BlockId) → __int64` | ✅ | ID движка блока |
| `InitBlock` | `(__int64 BlockId)` | ✅ | Переинициализация блока |
| `BlockAfterEdit` | `(__int64 BlockId)` | ✅ | Сигнал редактору о изменении |
| `ExecutePropScript` | `(__int64 BlockId, __int64 DataId)` | ✅ | Выполнить скрипт свойства |
| `GetPageObjectCount` | `(__int64 ProjectId) → long` | ✅ | Количество объектов на странице |
| `GetPageBlockId` | `(__int64 ProjectId, long BlockIndex) → __int64` | ✅ | ID блока по индексу |

### Имена классов для CreateBlock (подтверждённые)

| Имя класса | Описание |
|------------|----------|
| `Константа` | Блок-константа (свойство "a") |
| `Язык программирования` | Блок-скрипт (свойство "Code") |
| `Порт выхода` | Output port block |
| `Сумматор` | Сумматор |
| `Передаточная функция` | Передаточная ф-я (Laplace) |
| `Demultiplexor_vec` | Демультиплексор (разбивка шины) |

### Подтверждённые свойства блоков

| Свойство | Блок | Описание |
|----------|------|----------|
| `name` | Любой | Имя блока |
| `a` | Константа | Значение константы (строка!) |
| `Code` | Язык программирования | Текст скрипта Pascal |
| `Color` | Любой | Цвет блока (int → PASS/FAIL: 65280=зелёный, 255=красный) |
| `value` | Порт выхода | Выходное значение (double) |
| `x` | Любой | Координата X |
| `y` | Любой | Координата Y |

---

## 6. Порты и соединения

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetInPort` | `(__int64 BlockId, long InNo) → __int64` | ✅ | Получить входной порт по номеру |
| `GetOutPort` | `(__int64 BlockId, long OutNo) → __int64` | ✅ | Получить выходной порт по номеру |
| `GetPortCount` | `(__int64 BlockId) → long` | ✅ | Количество портов блока |
| `SetPortCount` | `(__int64 BlockId, long Count, long DefaultMode, long DefaultType, long DefaultSide)` | ✅ | Изменить количество портов |
| `SetCondPortCount` | `(...)` | ➖ | Условное количество портов |
| `GetPortInfo` | `(__int64 PortId) → (Name, Side, Mode, TypeId, ItemId, ...)` | ✅ | Полная информация о порте (12 полей) |
| `GetBlockPort` | `(__int64 BlockId, long Index) → __int64` | ✅ | Порт блока по индексу |
| `SetPortSide` | `(__int64 PortId, long Side)` | ✅ | Сторона порта (left/right/top/bottom) |
| `SetPortInverse` | `(__int64 PortId, long Inverse)` | ✅ | Инвертировать порт |
| `SetPortItemId` | `(__int64 PortId, long ItemId)` | ✅ | ID элемента порта |
| `SetPortMode` | `(__int64 PortId, long Mode)` | ✅ | Режим порта |
| `SetPortName` | `(__int64 PortId, BSTR PortName)` | ✅ | Имя порта |
| `SetPortLineTypeId` | `(__int64 PortId, long LineTypeId)` | ✅ | Тип линии порта |
| `SetPortInvisible` | `(__int64 PortId, long Invisible)` | ✅ | Скрыть порт |
| `CreateWire` | `(__int64 ProjectId, long LayerNo, long WireType, ...) → __int64` | ✅ | Создать провод между портами |
| `SetWirePoint` | `(__int64 WireId, long PointNo, double X, double Y)` | ✅ | Точка излома провода |
| `NormalizeWire` | `(__int64 WireId)` | ✅ | Нормализовать провод |

---

## 7. Страницы и субмодели

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetMainPage` | `(__int64 ProjectId) → __int64` | ✅ | ID главной страницы |
| `SetCurrentPage` | `(__int64 ProjectId, __int64 PageId)` | ✅ | Активировать страницу (нужно для доступа к блокам) |
| `GetCurentPage` | `(__int64 ProjectId) → __int64` | ➖ | ID текущей страницы |
| `GetSubmodelPage` | `(__int64 BlockId) → __int64` | ✅ | Страница субмодели (внутрь Macro_2) |
| `PageUp` | `(__int64 PageId) → __int64` | ✅ | Родительская страница |
| `LoadSubmodel` | `(__int64 BlockId, BSTR FileName)` | ✅ | Загрузить субмодель в блок |
| `AssignSubmodel` | `(__int64 ProjectId, __int64 BlockId, BSTR FileName)` | ✅ | Назначить субмодель |
| `SaveProjectBinary` | `(__int64 PrjId, BSTR FileName)` | ✅ | Сохранить как .prt |
| `SaveProjectXML` | `(__int64 PrjId, BSTR FileName)` | ✅ | Сохранить как XML (для отладки) |
| `SetPageScript` | `(__int64 ProjectId, BSTR Script, long CompileNow)` | ✅ | Скрипт страницы |
| `SetPageWindow` | `(__int64 PageId, Left, Top, Width, Height)` | ✅ | Окно страницы |
| `SetPageCoords` | `(__int64 PageId, double X, double Y, double Scale)` | ✅ | Координаты страницы |
| `ShowAllBlocks` | `(__int64 ProjectId)` | ✅ | Показать все блоки |
| `RepaintEditor` | `(__int64 ProjectId)` | ✅ | Перерисовать редактор |
| `ClearProjectActions` | `(__int64 ProjectId)` | ✅ | Очистить действия |

---

## 8. Pack (многопроектный режим)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `GetPackCount` | `() → long` | ✅ | Количество открытых паков |
| `GetPackId` | `(long PackNumber) → __int64` | ✅ | ID пака по индексу |
| `GetPackIdByFileName` | `(BSTR PackFileName) → __int64` | ✅ | ID пака по имени |
| `OpenPack` | `(BSTR FileName) → __int64` | ✅ | Открыть .pak файл |
| `ClosePack` | `(__int64 PackId)` | ✅ | Закрыть пак |
| `FindPackSignal` | `(__int64 PackId, BSTR SignalName) → TDataDescriptor` | ✅ | Найти сигнал в паке |
| `PackStart` | `(__int64 PackId)` | ✅ | Старт всех проектов пака |
| `PackRun` | `(__int64 PackId)` | ✅ | Запуск всех проектов |
| `PackPause` | `(__int64 PackId)` | ✅ | Пауза всех проектов |
| `PackStop` | `(__int64 PackId)` | ✅ | Остановка всех проектов |
| `PackStep` | `(__int64 PackId)` | ✅ | Один шаг всех проектов |
| `RunToPack` | `(__int64 PackId, double TargetTime)` | ✅ | RunTo для пака |
| `WaitForTimePack` | `(__int64 PackId, double TargetTime)` | ✅ | WaitForTime для пака |
| `SetRealTimeDelayPack` | `(__int64 PackId, long Flag, double Scale)` | ✅ | Синхронизация с реальным временем |
| `PackGetProjCount` | `(__int64 PackId) → long` | ✅ | Количество проектов в паке |
| `PackGetProjectIdByIndex` | `(__int64 PackId, long Index) → __int64` | ✅ | ID проекта по индексу |

---

## 9. Exchange File (файловый обмен данными)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `OpenExchangeFile` | `(TExchangeMethod, BSTR FileName) → __int64` | ✅ | Открыть файл обмена |
| `CloseExchangeFile` | `(__int64 EngineId)` | ➖ | Закрыть файл обмена (type issue под Wine) |
| `AddToReadList` | `(TDataDescriptor)` | ➖ | Добавить в список чтения |
| `AddToWriteList` | `(TDataDescriptor)` | ➖ | Добавить в список записи |
| `ClearReadList` | `()` | ✅ | Очистить список чтения |
| `ClearWriteList` | `()` | ✅ | Очистить список записи |
| `ReadList` | `(__int64 EngineId)` | ✅ | Выполнить чтение |
| `WriteList` | `(__int64 EngineId)` | ✅ | Выполнить запись |
| `Read` | `(THandleArray, __int64 EngineId)` | ➖ | Чтение через handle-массив (type issue под Wine) |
| `Write` | `(THandleArray, __int64 EngineId)` | ➖ | Запись через handle-массив (type issue под Wine) |

TExchangeMethod: `exmFile = 0`, `exmMemMapFile = 1`

---

## 10. Управление формой/окном

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `FormShow` | `(__int64 ProjectId)` | ✅ | Показать форму |
| `FormHide` | `(__int64 ProjectId)` | ✅ | Скрыть форму |
| `FormBringToFront` | `(__int64 ProjectId)` | ✅ | На передний план |
| `FormSendToBack` | `(__int64 ProjectId)` | ✅ | На задний план |
| `GetFormVisible` | `(__int64 ProjectId) → long` | ➖ | Видимость формы (нужен ProjectId, не `()`) |
| `GetFormState` | `(__int64 ProjectId) → long` | ➖ | Состояние формы (нужен ProjectId) |
| `SetFormState` | `(__int64 ProjectId, long Value)` | ✅ | Установить состояние |
| `GetFormHandle` | `(__int64 ProjectId) → __int64` | ➖ | HWND формы (нужен ProjectId) |
| `GetFormCoords` | `(__int64 ProjectId) → (Left, Top, Right, Bottom, Xcenter, Ycenter, Scale)` | ➖ | Координаты формы (нужен ProjectId) |
| `SetFormCoords` | `(__int64 ProjectId, ...)` | ➖ | Установить координаты (есть Flags) |
| `GetFormStyle` | `(__int64 ProjectId) → long` | ➖ | Стиль формы (нужен ProjectId) |
| `SetFormStyle` | `(__int64 ProjectId, long Value)` | ✅ | Установить стиль |
| `GetFormBorderStyle` | `(__int64 ProjectId) → long` | ➖ | Стиль рамки (нужен ProjectId) |
| `SetFormBorderStyle` | `(__int64 ProjectId, long Value)` | ✅ | Установить стиль рамки |
| `SetFormCaption` | `(__int64 ProjectId, BSTR ACaption)` | ✅ | Заголовок формы |
| `SetGraphicView` | `(__int64 ProjectId, long Left, ...)` | ➖ | Графическое отображение |
| `SetMainFormVisible` | `(long Value)` | ✅ | Видимость главного окна |

---

## 11. Рестарт (checkpoint/restore)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `WriteProjectRestart` | `(__int64 ProjectId, BSTR FileName)` | ✅ | Сохранить рестарт |
| `ReadProjectRestart` | `(__int64 ProjectId, BSTR FileName)` | ✅ | Загрузить рестарт |
| `ResetProjectEngines` | `(__int64 ProjectId)` | ✅ | Сброс движков |
| `WriteRestartPoint` | `(__int64 ProjectId)` | ✅ | Точка рестарта |
| `ReadRestartPoint` | `(__int64 ProjectId)` | ✅ | Восстановить точку |
| `SetProjectReadRestartFile` | `(...)` | ➖ | Настройка чтения рестарта (есть fLoadRst) |
| `SetProjectWriteRestartFile` | `(...)` | ➖ | Настройка записи рестарта (есть fSaveRst) |
| `GetProjectRestartNames` | `(__int64 ProjectId) → (readFile, writeFile, ...)` | ➖ | Имена файлов рестарта (нужен ProjectId) |
| `SetRestartPreserveFlag` | `(__int64 ProjectId, long Flag)` | ✅ | Флаг сохранения рестарта |

---

## 12. Системные методы

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `SetSilentMode` | `(long Mode)` | ✅ | 1 = без GUI, 0 = с GUI |
| `SetShutdownOnLastRelease` | `(long Value)` | ✅ | Завершать процесс при последнем Release |
| `SetNoCloseAppFlag` | `(long Value)` | ✅ | Не закрывать приложение при закрытии проекта |
| `SetNoCloseFlag` | `(__int64 ProjectId, long Value)` | ✅ | Per-project no-close |
| `SetDesktopAsParent` | `(long Value)` | ✅ | Desktop как родительское окно |
| `SetReadOnlyFlag` | `(__int64 ProjectId, long ReadOnlyFlag)` | ✅ | Read-only режим |
| `GetEditorFlags` | `(__int64 ProjectId) → (Modified, Saved, PageModified, ReadOnly)` | ➖ | Флаги редактора (нужен ProjectId) |
| `GetProjectStateFlag` | `(__int64 ProjectId) → long` | ➖ | Флаг состояния проекта (нужен ProjectId) |
| `GetProcessID` | `() → unsigned long` | ✅ | PID процесса SimInTech |
| `SetSystemVariable` | `(BSTR aVarName, BSTR aValue)` | ✅ | Системная переменная |
| `GetSystemVariableValue` | `(BSTR aVarName) → BSTR` | ✅ | Чтение системной переменной |
| `SetSysProp` | `(...)` | ➖ | Системные свойства проекта |
| `SetLayerProp` | `(__int64 ProjectId, long LayerNo, BSTR PropName, BSTR StrValue)` | ✅ | Свойства слоя |
| `SetProjectModified` | `(__int64 ProjectId, long AModified)` | ✅ | Флаг модификации |
| `SetParentPrjHandle` | `(__int64 ProjectId, __int64 Handle)` | ✅ | Parent handle |
| `SetPrjPosByPrjId` | `(__int64 SrcPrjId, __int64 DestPrjId)` | ✅ | Позиция проекта |
| `SetRealTimeDelay` | `(__int64 ProjectId, long Flag, double Scale)` | ❌ | Не реализован в этой версии SimInTech |
| `WaitForAllLoading` | `()` | ✅ | Ждать загрузки всех ресурсов |
| `ProcessAllMessages` | `()` | ✅ | Обработка сообщений Windows |

---

## 13. База данных и плагины

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `ExportDBToXML` | `(__int64 ProjectId, BSTR DBFileName) → long` | ✅ | Экспорт БД в XML |
| `GetProjectDB` | `(__int64 ProjectId) → (DBPlugin, DBName)` | ➖ | Информация о БД проекта (нужен ProjectId) |
| `ReloadProjectDB` | `(__int64 ProjectId, BSTR DBPluginName, BSTR DBName)` | ✅ | Перезагрузить БД |
| `SetDBOverride` | `(BSTR aOverrideDBPlugin, BSTR aOverrideDBName)` | ✅ | Переопределение БД |
| `GetLayerName` | `(__int64 ProjectId, long LayerNumber) → BSTR` | ✅ | Имя слоя |
| `SendPluginCommand` | `(BSTR PluginName, long CommandId, BSTR CommandStr, __int64 ObjId) → (ResultStr, ResultPtr)` | ✅ | Команда плагину |

---

## 14. Примитивы (графика)

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `CreatePrimitiv` | `(__int64 ProjectId, long LayerNo, __int64 Parent, long PrimitivId) → __int64` | ✅ | Создать примитив |
| `AddObjectPoint` | `(__int64 BlockId, double X, double Y)` | ✅ | Добавить точку |
| `InsertObjectPoint` | `(__int64 BlockId, long Index, double X, double Y)` | ✅ | Вставить точку |
| `DeleteObjectPoint` | `(__int64 BlockId, long Index, long Count)` | ✅ | Удалить точки |
| `GetPointCount` | `(__int64 BlockId) → long` | ✅ | Количество точек |

---

## 15. Утилиты

| Метод | Сигнатура | Статус | Описание |
|-------|-----------|--------|----------|
| `SetJournalSavePeriod` | `(long Period)` | ➖ | Период сохранения журнала (мс) |
| `SetPipeName` | `(BSTR PipeName)` | ➖ | Имя пайпа для лога |
| `SetCallbackHandleAndDataID` | `(__int64 CallbackHWND, __int64 CallBackDataId)` | ✅ | Callback для событий |
| `SetCmdStr` | `(__int64 ProjectId, BSTR CmdStr)` | ✅ | Команда проекту |
| `GetCmdStr` | `(__int64 ProjectId) → BSTR` | ➖ | Чтение команды (нужен ProjectId) |
| `WriteAsFont` | `(TDataDescriptor, BSTR FontName, long FontSize, byte FontStyle)` | ➖ | Запись шрифта (нужен desc) |
| `SetFontData` | `(__int64 PropHandle, BSTR FontName, long Height, long Color, byte Style)` | ✅ | Данные шрифта |

---

## 16. Рекомендуемый порядок работы (проверенный workflow)

```
1. SetSilentMode(1)
2. OpenProject("path.xprt") → pj_id
3. GetProjectIdByNumber(0) → pj_id (защита от дублирования ID)
4. GetMainPage(pj_id) → page_id
5. SetCurrentPage(pj_id, page_id)
6. FindSignalData("name", pj_id) → desc  (для каждого сигнала)
7. WriteAsFloat(desc, value)  (запись входов ДО старта)
8. ProjectStart(pj_id)
9. ProjectRun(pj_id) или RunTo(pj_id, time) или ProjectStep(pj_id)
10. ReadAsFloat(desc) → value (чтение выходов)
11. ProjectStop(pj_id)
12. CloseProject(pj_id)
```

### Порядок с WriteAsFloat между шагами (динамическое управление)

```
1-8. Как выше
9. ProjectRun(pj_id)
10. poll GetProjectTime(pj_id)
11. WriteAsFloat(desc_in, new_value)  (изменение входа на лету)
12. ProjectStep(pj_id)
13. ReadAsFloat(desc_out) → value
14. goto 10 или ProjectStop
```

---

## 17. Критические ограничения

| Проблема | Причина | Решение |
|----------|---------|---------|
| pywin32 не работает | VT_RECORD marshalling broken | comtypes |
| SetBlockProp("a") не влияет на симуляцию | Константы инициализируются до ProjectStart | WriteAsFloat напрямую |
| Color не читается из скрипта блока | Color - design-time property | Читать через COM API после остановки |
| OpenProject не принимает .xprt? | Баг COM API - некоторые версии | Открывать .prt или использовать SaveProjectBinary |
