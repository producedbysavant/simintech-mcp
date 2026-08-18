# Встроенный язык SimInTech — справочник

Перенесено из `simintech-code-library` (архивирован 2026-08).

Справочник по **встроенному языку программирования SimInTech** — механизму
создания блоков и схем скриптом (`createblock`/`createmodel`). Это **отдельный
от COM API** способ построения моделей; используется как резервный путь для
классов блоков, не создаваемых через `CreateBlock` (см.
`simintech_api/constants.py:UNSUPPORTED_COM_BLOCK_CLASSES`).

## Содержание

| Раздел | Описание |
|--------|----------|
| `language/` | Синтаксис, ключевые слова, подводные камни, примеры |
| `blocks/` | Примеры блоков: генераторы, конвертеры, индикаторы |
| `patterns/` | Типовые схемы: ПИД, обратная связь, пространство состояний |
| `tutorials/` | Пошаговые руководства: первая модель, переменные, субмодели |

## Быстрый старт

```c
// Простейшая программа SimInTech (блок «Язык программирования»)
input u;
output y;
y = u * 2.5;
```

```c
// ПИД-регулятор
const Kp = 1.5, Ki = 0.8, Kd = 0.3;
var error, integral, derivative;
var prev_error = 0;
init integral = 0;
input setpoint, measurement;
output control;

error = setpoint - measurement;
integral = integral + error * h;
derivative = (error - prev_error) / h;
control = Kp * error + Ki * integral + Kd * derivative;
prev_error = error;
```

## Создание схемы скриптом

```c
block0 = createblock(getcurrentprojectid, "Порт входа");
setprop(block0, "Points", [(-150, 50)]);
initobject(block0);
block1 = createblock(getcurrentprojectid, "Усилитель");
setprop(block1, "Points", [(0, 0)]);
setprop(block1, "a", 10);
wire = createwire(getcurrentprojectid, 0, 0, 0,
                  getoutportid(block0, 0), getinportid(block1, 0), 0);
normalizewire(wire);
```

Декларативная форма (вход `createmodel`):

```
block0: ( type = "Ступенька", points=[(-200,0),...], t=[2], y0=[0], yk=[3] )
wire:   ( type="wire", points=[], src="block1:out:0", dst="block2:in:0" )
```
