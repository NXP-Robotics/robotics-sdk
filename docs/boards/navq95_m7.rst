navq95_m7 (i.MX95 M7)
=====================

The NavQ95 realtime domain runs on the i.MX95 Cortex-M7. It supports the
**Cerebri** (CogniPilot) and plain **Zephyr** stacks. The NavQ95 A-core Ubuntu
image is delivered separately as a WIC (see :doc:`../prebuilt_binaries`).

Build
-----

.. code-block:: console

   west sdk build --platform navq95_m7                 # Cerebri (default)
   west sdk build --platform navq95_m7 --stack zephyr  # plain Zephyr

Artifacts:

* ``navq95.elf`` / ``navq95.bin``          -- M7 image (TCM)
* ``navq95_ddr.elf`` / ``navq95_ddr.bin``  -- M7 image running from DDR

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform navq95_m7   # firmware + ubuntu .wic

See :doc:`getting_started` for the required one-time ``west init`` bootstrap.
