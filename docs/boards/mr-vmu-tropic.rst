mr-vmu-tropic (PX4/Zephyr)
==================

The mr-vmu-tropic flight/motor controller is built with the **PX4** stack
(default) and may also be targeted with plain **Zephyr**.

Build
-----

.. code-block:: console

   west sdk build --platform mr-vmu-tropic                # PX4 (default)
   west sdk build --platform mr-vmu-tropic --stack zephyr # plain Zephyr

PX4 builds shell out to ``make nxp_mr-tropic_default``.

Artifacts:

* ``nxp_mr-tropic_bootloader.bin`` -- bootloader
* ``nxp_mr-tropic_default.bin``    -- application firmware
* ``nxp_mr-tropic_default.px4``    -- QGroundControl update image

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform mr-vmu-tropic

See :doc:`getting_started` for the required one-time ``west init`` bootstrap.
