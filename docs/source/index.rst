simintech-api documentation
============================

Python-библиотека для программного управления **SimInTech** через внешний
COM API (`IMVTU_Server`).

Содержание:

.. toctree::
   :maxdepth: 2

   guide
   api
   algorithms
   examples


Обзор
-----

Библиотека позволяет ИИ-агенту или пользователю:

* создавать проекты SimInTech и размещать блоки из стандартных библиотек;
* настраивать параметры блоков;
* соединять блоки линиями связи;
* запускать расчёт, читать и записывать сигналы;
* автоматически расставлять блоки (``LayeredPlacer``) и трассировать линии
  (``AStarRouter``) без наложений и пересечений с блоками.

Быстрый старт
-------------

.. code-block:: python

   from simintech_api import COMClient, Project

   client = COMClient(silent_mode=True).connect()
   prj = Project.new(client)
   page = prj.get_main_page()

   b1 = page.create_block("Константа", 0, 0)
   b1.set_property("y0", 5.0)
   b2 = page.create_block("Усилитель", 200, 0)
   b2.set_property("a", 2.0)
   b1.connect(b2)

   prj.save_xml("model.xprt")
   prj.close()
   client.shutdown()
