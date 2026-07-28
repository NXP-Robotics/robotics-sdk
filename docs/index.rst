NXP Robotics SDK
================

The NXP Robotics SDK is a thin superproject that unifies multiple robotics
platforms across the **PX4**, **CogniPilot Cerebri**, and **Zephyr** stacks.
It pins exact upstream revisions, dispatches builds to the right build system,
and provides prebuilt artifacts through Nexus.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting_started
   boards/navq95_m7
   boards/tropic
   boards/vmu-rt1176
   prebuilt_binaries
   release_notes

Supported platforms and stacks
-------------------------------

.. list-table::
   :header-rows: 1

   * - Platform
     - PX4
     - Cerebri
     - Zephyr
   * - ``navq95_m7``
     -
     - yes
     - yes
   * - ``tropic``
     - yes
     -
     - yes
   * - ``vmu-rt1176``
     - yes
     - yes
     - yes
