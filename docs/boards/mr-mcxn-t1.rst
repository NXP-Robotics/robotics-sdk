mr-mcxn-t1 (MR-MCXN-T1, MCXN947)
================================

The ``mr-mcxn-t1`` platform targets the NXP **MR-MCXN-T1** robotics board
(MCXN947, Cortex-M33). It supports two ways of working, each a build stack:

* **zephyr** (default) -- build stock Zephyr samples for board / peripheral
  bring-up and validation.
* **cerebri** -- build CogniPilot Cerebri integration apps (currently
  ``optical_flow``).

Shields
-------

For the **zephyr** stack a user-facing ``--shield {t1,oflo,f9p}`` selects the
example set. The shields are **mutually exclusive** -- a shield occupies the
expansion connector, so the I/O breakout peripherals are reachable only on
``t1``:

* ``t1``   -- base board + I/O breakout (default).
* ``oflo`` -- optical-flow shield (ToF, optical-flow, IMUs).
* ``f9p``  -- u-blox F9P GNSS shield.

For the **cerebri** stack the shield is implied by the app (the SDK injects the
matching shield overlay), so ``--shield`` is not passed.

MCUboot (sysbuild)
------------------

Every zephyr / cerebri example is built with **sysbuild** and produces both the
MCUboot bootloader (``mcuboot/zephyr/zephyr.bin``) and the runnable, signed
application (``<app>/zephyr/zephyr.signed.bin``).

Build
-----

.. code-block:: console

   west sdk build --platform mr-mcxn-t1                       # zephyr, --shield t1 (default)
   west sdk build --platform mr-mcxn-t1 --shield oflo         # zephyr, optical-flow shield
   west sdk build --platform mr-mcxn-t1 --shield f9p          # zephyr, F9P GNSS shield
   west sdk build --platform mr-mcxn-t1 --shield t1 <example> # a specific example
   west sdk build --platform mr-mcxn-t1 --stack cerebri       # Cerebri (ROS2 nodes: optical_flow and gnss)

With no ``<example>`` the curated CI / default set is built:
``hello_world`` and ``dhcpv4_client`` (t1), ``gnss`` (f9p) and
``optical_flow`` (cerebri). Every other example stays buildable by naming it
explicitly.

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform mr-mcxn-t1

The published (default-set) firmware lands under ``bin/mr-mcxn-t1/``,
preserving the ``<stack>/<shield>/<example>/`` layout, with the signed app
(``zephyr.signed.bin``) and its MCUboot bootloader (``mcuboot.bin``) per
example.

See :doc:`../getting_started` for the required one-time ``west init`` bootstrap
and the Zephyr SDK / ``dtc`` prerequisites.
