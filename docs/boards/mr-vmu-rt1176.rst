mr-vmu-rt1176 (CogniPilot VMU, RT1176)
===================================

The CogniPilot Vehicle Management Unit on the NXP RT1176 supports all three
stacks: **PX4** (default), **Cerebri**, and plain **Zephyr**.

Build
-----

.. code-block:: console

   west sdk build --platform mr-vmu-rt1176                  # PX4 (default)
   west sdk build --platform mr-vmu-rt1176 --stack cerebri  # Cerebri
   west sdk build --platform mr-vmu-rt1176 --stack zephyr   # plain Zephyr

PX4 builds shell out to ``make nxp_fmurt1176_default``.

Artifacts:

* ``mr-vmu-rt1176.elf`` / ``mr-vmu-rt1176.bin``

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform mr-vmu-rt1176

See :doc:`getting_started` for the required one-time ``west init`` bootstrap.
