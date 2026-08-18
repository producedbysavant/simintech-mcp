# Туториал 2: Переменные и типы данных

Разбираемся с типами данных, переменными и областями видимости во встроенном языке SimInTech.

## Что ты узнаешь

- Какие типы данных поддерживает SimInTech
- Как объявлять и инициализировать переменные
- Что такое область видимости
- Как работают массивы

## Типы данных

SimInTech поддерживает четыре основных типа:

```simintech
var:
  int      count = 0;          // Целое число (32 бита)
  double   temperature = 36.6; // Число с плавающей точкой
  bool     isReady = false;    // Логическое (true/false)
  string   name = "sensor_1";  // Строка
```

## Практика: блок с различными типами

Создадим блок, который использует все типы данных:

```simintech
function DataTypeDemo
  input:
    double analogInput;
    bool digitalInput;
  output:
    double analogOutput;
    bool digitalOutput;
  var:
    int counter = 0;
    double sum = 0.0;
    double average;
    string statusMessage = "OK";
    bool limitReached = false;
  begin:
    // Инкремент счётчика
    counter = counter + 1;

    // Накопление суммы
    sum = sum + analogInput;

    // Среднее значение
    average = sum / double(counter);  // явное приведение

    // Логика: если среднее превысило порог
    if average > 100.0 then
      limitReached = true;
      statusMessage = "LIMIT";
    end_if;

    // Выходы
    analogOutput = average;
    digitalOutput = limitReached;
  end;
```

## Преобразование типов

### Явное преобразование

```simintech
var:
  double x = 3.14159;
  int n;
  string s;
begin:
  n = int(x);        // 3 (с отбрасыванием дробной части)
  s = rtoa(x);       // "3.14159" (double → string)
  x = ator("2.5");   // 2.5 (string → double)
  n = atoi("42");    // 42 (string → int)
  s = itoa(n);       // "42" (int → string)
```

### Неявное преобразование

```simintech
var:
  int i = 5;
  double d;
begin:
  d = i;             // неявно: d = 5.0
  i = int(d);        // явно: i = 5 (без явного приведения будет ошибка компиляции)
```

## Массивы

### Одномерные массивы

```simintech
var:
  double readings[10];          // объявление без инициализации
  double values[4] = {1.0, 2.0, 3.0, 4.0};  // с инициализацией
  int i;
  double sum = 0.0;

init:
  // Инициализация массива
  for i = 0 to 9 do
    readings[i] = 0.0;
  end_for;

begin:
  // Использование
  values[2] = 10.0;            // запись
  sum = values[0] + values[1]; // чтение
```

### Двумерные массивы (матрицы)

```simintech
var:
  double matrix[3][3];
  double identity[3][3] = {{1.0, 0.0, 0.0},
                           {0.0, 1.0, 0.0},
                           {0.0, 0.0, 1.0}};
  int i, j;
  double sum = 0.0;

begin:
  // Обход матрицы
  for i = 0 to 2 do
    for j = 0 to 2 do
      sum = sum + identity[i][j];
    end_for;
  end_for;
```

## Область видимости

### Глобальные переменные

Объявляются в схеме (не внутри функции). Доступны всем блокам.

Как создать:
1. Правый клик на схеме → **«Добавить глобальную переменную»**
2. Введи имя и тип
3. Используй в любой функции как `::globalVarName`

```simintech
// В любом блоке схемы:
y = ::systemGain * u;  // :: — префикс глобальной переменной
```

### Локальные переменные

Объявляются в секции `var` функции. Доступны только внутри этой функции.

```simintech
function MyBlock
  var:
    double localVar = 0.0;  // локальная — видна только здесь
  begin:
    localVar = localVar + 1.0;
  end;
```

### Статические переменные

Все переменные в секции `var` сохраняют значение между шагами (они статические по умолчанию).

```simintech
function Counter
  output:
    int count;
  var:
    int internalCount = 0;  // сохраняется между шагами!
  begin:
    internalCount = internalCount + 1;
    count = internalCount;
  end;
```

## Практическое задание

Создай блок, который:
1. Принимает 3 аналоговых входа
2. Вычисляет их среднее, минимум и максимум
3. Хранит историю последних 10 значений
4. Выдаёт true, если среднее за 10 шагов превышает порог

```simintech
function SignalAnalyzer
  input:
    double ch1;
    double ch2;
    double ch3;
    double threshold = 50.0;
  output:
    double average;
    double minimum;
    double maximum;
    bool alarm;
  var:
    double history[10] = {0.0};
    int index = 0;
    int i;
    double sum;
    int count;
  begin:
    // Текущие значения
    average = (ch1 + ch2 + ch3) / 3.0;
    minimum = min(min(ch1, ch2), ch3);
    maximum = max(max(ch1, ch2), ch3);

    // История
    history[index] = average;
    index = index + 1;
    if index >= 10 then
      index = 0;
    end_if;

    // Среднее за 10 шагов
    sum = 0.0;
    count = 10;
    for i = 0 to 9 do
      sum = sum + history[i];
    end_for;

    // Сигнализация
    if sum / double(count) > threshold then
      alarm = true;
    else
      alarm = false;
    end_if;
  end;
```

## Проверь себя

1. Какой тип данных выберешь для хранения денежной суммы? → `double`
2. Что будет при `int x = 3.14;`? → Ошибка компиляции (нужно явное приведение)
3. Как обратиться к третьему элементу массива `arr`? → `arr[2]` (индексация с 0)
4. Сохраняется ли значение переменной между шагами? → Да, если она в `var`

## Типичные ошибки

```simintech
// Ошибка 1: выход за границы массива
double arr[5];
arr[5] = 1.0;  // Индекс 5 при размере 5 — недопустимо!

// Ошибка 2: неявное приведение int → double наоборот
int x = 5.7;   // Ошибка — нужно int(5.7)

// Ошибка 3: использование неинициализированной переменной
double sum;
sum = sum + 1.0;  // sum содержит мусор!
```

## Что дальше?

Теперь ты знаешь всё о переменных и типах. Переходи к туториалу 3: «Субмодели: создаём макроблоки»
