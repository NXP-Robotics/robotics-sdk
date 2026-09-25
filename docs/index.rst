NXP Robotics SDK
================

The NXP Robotics SDK is a thin superproject that unifies multiple robotics
platforms across the **PX4**, **CogniPilot Cerebri**, **Zephyr**, and **AM32**
stacks. It pins exact upstream revisions, dispatches builds to the right build
system, and provides prebuilt artifacts.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   getting_started
   building_with_docker
   boards/mr-navq95
   boards/mr-vmu-tropic
   boards/mr-vmu-rt1176
   boards/mr-esc-am32-mcxa133
   boards/mr-mcxn-t1
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
     - AM32
   * - ``mr-navq95``
     - yes
     -
     - yes
     -
   * - ``mr-vmu-tropic``
     - yes
     -
     - yes
     -
   * - ``mr-vmu-rt1176``
     - yes
     - yes
     - yes
     -
   * - ``mr-esc-am32-mcxa133``
     -
     -
     -
     - yes
   * - ``mr-mcxn-t1``
     -
     - yes
     - yes
     -
