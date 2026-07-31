navq95 (i.MX95 M7)
==================

The NavQ95 (MR-NavQ95B) realtime domain runs on the i.MX95 Cortex-M7. It
supports the **PX4** (default) and plain **Zephyr** stacks. The NavQ95 A-core
Ubuntu image is delivered separately as a WIC (see :doc:`../prebuilt_binaries`).

.. note::

   NavQ95 PX4 support is built from NXP's public PX4 fork
   (``NXP-Robotics/PX4-Autopilot``, board ``imx95``), cloned over HTTPS with no
   credentials required. See ``manifests/px4/navq95.yml`` for the pinned
   remote and revision.

Build
-----

.. code-block:: console

   west sdk build --platform navq95                 # PX4 (default)
   west sdk build --platform navq95 --stack zephyr  # plain Zephyr

PX4 artifacts (collected into ``bin/navq95/realtime/``; the Ubuntu WIC
downloads to ``bin/navq95/ubuntu/``):

* ``nxp_imx95_default.elf`` / ``nxp_imx95_default.bin``  -- M7 image (NOR Flash)
* ``nxp_imx95_ddr.elf`` / ``nxp_imx95_ddr.bin``          -- M7 image from DDR

MCUboot signing (PX4, NavQ95 only)
----------------------------------

As a post-build step, the PX4 default ``.bin`` image is signed with
MCUboot's ``imgtool`` (shipped inside the PX4 tree at ``scripts/imgtool.py``),
producing a ``.mcuboot.bin`` alongside the raw ``.bin``:

* ``nxp_imx95_default.mcuboot.bin``

.. note::

   Only the **default** image is signed. The **DDR** image
   (``nxp_imx95_ddr.bin``) is intentionally **not** signed.

The signing parameters live in the ``sign:`` block of
``platforms/navq95/build.yml`` and are applied automatically -- equivalent
to:

.. code-block:: console

   ./imgtool.py sign --version 1.0.0 --header-size 0x800 \
       --slot-size 0x700000 --pad-header \
       nxp_imx95_default.bin nxp_imx95_default.mcuboot.bin

This step is **NavQ95-only**: it is driven entirely by the ``sign:`` key in the
platform's ``build.yml`` (absent for other platforms), so no signing is applied
to ``tropic`` or ``vmu-rt1176``. The SDK installs imgtool's Python dependencies
(``click``, ``cryptography``, ``intelhex``, ``pyyaml``, ``cbor2``) into the
per-workspace venv automatically.

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform navq95   # firmware (raw + signed) + ubuntu .wic

See :doc:`getting_started` for the required one-time ``west init`` bootstrap.
