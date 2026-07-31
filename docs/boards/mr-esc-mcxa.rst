mr-esc-mcxa (AM32 on MCXA)
==========================

The ``mr-esc-mcxa`` platform builds the open-source **AM32** ESC /
motor-controller firmware for NXP **MCXA** targets. It currently supports the
**FRDM-MCXA153** board (Cortex-M33), whose AM32 make target is ``FRDM_A153``.

The AM32 stack uses a GNU Makefile build and downloads its own
**xPack arm-none-eabi-gcc** toolchain into the source tree via a one-time
``make arm_sdk_install`` step (handled automatically as a prebuild).

.. note::

   AM32 sources are currently tracked directly on upstream ``main`` (no release
   pin yet); see ``manifests/am32/mr-esc-mcxa.yml``. Pin to an exact tag/SHA at
   release time.

Build
-----

.. code-block:: console

   west sdk build --platform mr-esc-mcxa            # AM32 (default stack)
   west sdk build --platform mr-esc-mcxa --stack am32

On first build, ``make arm_sdk_install`` downloads the xPack toolchain (needs
network + ``wget``/``tar``); subsequent builds skip it.

Artifacts (collected **as-is**, with the AM32 version in the filename, into
``bin/mr-esc-mcxa/``):

* ``AM32_FRDM_A153_<version>.elf``
* ``AM32_FRDM_A153_<version>.bin``
* ``AM32_FRDM_A153_<version>.hex``

Download prebuilt
-----------------

.. code-block:: console

   west sdk download --platform mr-esc-mcxa

See :doc:`../getting_started` for the required one-time ``west init`` bootstrap
and the toolchain/host prerequisites (``make``, ``git``, ``wget``, ``tar``,
``python3``).
