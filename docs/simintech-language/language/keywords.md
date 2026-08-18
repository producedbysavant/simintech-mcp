# Ключевые слова SimInTech

## `const` — Константа

Значение задаётся один раз, не меняется во время расчёта.
Глобальные константы объявляются в скрипте проекта.

```simintech
const
    g = 9.81,
    pi = 3.1415926535,
    N = 100,
    tau = 0.5;
```

## `var` — Переменная

Сохраняет значение между шагами. Каждая переменная — с явным типом.
**Недопустимо:** `var a, b, c: double;` (список имён с одним типом).

```simintech
var
    counter: integer,
    integral: double,
    state[5]: array,
    phase_arr[NWAG]: intarray;

// Присваивание
counter = counter + 1;
integral = integral + error * dt;
```

## `input` — Вход порта

Читает значение из входного порта блока.

```simintech
input
    u: double,           // один скалярный вход
    speeds[3]: array,    // векторный вход (3 элемента)
    v: double,
    w: double;
```

## `output` — Выход порта

Записывает значение в выходной порт блока.

```simintech
output
    y: double,
    result[3]: array,
    error_code: integer;

y = u * Kp;
```

## `begin..end` — Блок кода

Обрамляет тело условия, цикла или основной код.

```simintech
if phase = PHASE_RUN then
begin
    timer = timer + dt;
    result = result + input_val * dt;
    if timer >= duration then
        phase = PHASE_DONE;
end;
```

## `initialized` — Однократная инициализация

Флаг для загрузки данных при первом шаге:

```simintech
var initialized: boolean;

if not initialized then
begin
    for (idx = 1, lengthofm(input_arr)) do
        static_arr[idx] = input_arr[idx];
    lenStatic = lengthofm(input_arr);
    initialized = true;
end;
```

Альтернативно: `if Nstep = 0 then ...`

## Примеры

```simintech
// Усилитель с ограничением
input u: double;
output y: double;
const
    K = 2.5,
    y_max = 10.0;
var y_raw: double;

begin
    y_raw = u * K;
    if y_raw > y_max then
        y = y_max
    else if y_raw < -y_max then
        y = -y_max
    else
        y = y_raw;
end
```

```simintech
// Интегратор с насыщением
input u: double;
output y: double;
const
    y_max = 100.0;
var y: double;

begin
    y = y + u * dt;
    if y > y_max then
        y = y_max
    else if y < -y_max then
        y = -y_max;
end
```
