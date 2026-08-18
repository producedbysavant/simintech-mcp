Руководство пользователя
========================

Создание первой модели через simintech-api.

Требования
----------

* **Windows** (COM API SimInTech работает только там).
* Установленный SimInTech 64 и зарегистрированный COM-объект:

  .. code-block:: text

     C:\\SimInTech64\\bin\\mmain.exe /regserver

* Python 3.9+, comtypes:

  .. code-block:: bash

     pip install -e ".[test]"

Подключение
-----------

.. code-block:: python

   from simintech_api import COMClient
   client = COMClient(silent_mode=True).connect()

``silent_mode=True`` скрывает UI. ``connect()`` бросает
``ComConnectionError``, если COM недоступен (не Windows / не /regserver).

Проект и блоки
--------------

.. code-block:: python

   from simintech_api import Project

   prj = Project.new(client)
   page = prj.get_main_page()

   b1 = page.create_block("Ступенька", 0, 0)
   b1.set_property("t", 0.0)
   b1.set_property("y0", 0.0)
   b1.set_property("yk", 5.0)

   b2 = page.create_block("Усилитель", 200, 0)
   b2.set_property("a", 2.0)

Имена классов — русские, регистрозависимы («Константа», «Усилитель»,
«Сумматор», «Интегратор», «Производная», «Ступенька», «Синусоида»,
«Временной график» и др.).

.. note::

   Некоторые классы («Из памяти», «Порт выхода», «Флаг входа в состояние»)
   **не создаются через COM CreateBlock** — библиотека бросает
   ``UnsupportedBlockError``. Для них используйте встроенный язык SimInTech.

Соединение и расчёт
-------------------

.. code-block:: python

   b1.connect(b2)                 # out0 -> b2.in0
   sim = prj.simulation()
   sim.start()                    # инициализация (обязательна!)
   sim.run_to(5.0)                # расчёт до 5 с
   sim.stop()

Сигналы
-------

.. code-block:: python

   sig = prj.signal("имя_сигнала")
   value = sig.read()             # тип по DataType
   sig.write(3.14)

   for info in prj.list_signals():
       print(info.name, info.caption)

.. warning::

   ``list_signals()`` и ``find_signal()`` работают **только после
   ``sim.start()``** (ProjectStart) — модель компилируется и сигналы
   появляются в списке. До старта список пуст.

Сохранение
----------

.. code-block:: python

   prj.save_xml("model.xprt")
   prj.close()
   client.shutdown()     # завершает процесс mmain.exe (если порождён нами)

Layout и трассировка
--------------------

.. code-block:: python

   from simintech_api.layout import LayeredPlacer, AStarRouter, ObstacleGrid

   positions = LayeredPlacer().place(block_ids, connections, sizes=sizes)
   grid = ObstacleGrid(200, 120)
   for bid, (cx, cy) in positions.items():
       w, h = sizes[bid]
       grid.add_rect(cx - w/2, cy - h/2, w, h)
   points = AStarRouter().route(p1, p2, grid, start_side=1, end_side=0)
   page.create_wire(port1, port2, points)

ИИ-агент / CLI
--------------

.. code-block:: python

   from simintech_api.agent import SimInTechAgent
   agent = SimInTechAgent()
   print(agent.execute("help"))

.. code-block:: text

   simintech-cli "create project \"Demo\"" "add block \"Ступенька\""
