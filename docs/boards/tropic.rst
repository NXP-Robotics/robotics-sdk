tropic (mr-tropic)
==================

The mr-tropic flight/motor controller is built with the **PX4** stack (default)
and may also be targeted with plain **Zephyr**.

Build
-----

.. code-block:: console

   west sdk build --platform tropic                 # PX4 (default)
   west sdk build --platform tropic --stack zephyr  # plain Zephyr

PX4 builds shell out to ``make nxp_mr-tropic_default``.

Artifacts:

* ``mr-tropic.bin``            -- application firmware
* ``mr-tropic.bootloader.bin`` -- bootloader

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform tropic

See :doc:`getting_started` for the required one-time ``west init`` bootstrap.
