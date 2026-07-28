Getting Started
===============

.. _Prerequisites:

Prerequisites
-------------

``west sdk build`` shells out to the native build systems (PX4's ``make`` and
Zephyr's ``west build``), so the corresponding toolchains and host packages
**must already be installed**. The SDK manages only the Python layer (a
per-workspace virtualenv with PX4's ``requirements.txt``); it does **not**
install system compilers or SDKs for you.

Before building, ``west sdk build`` runs a **preflight check** that verifies the
required tools are present and, if any are missing, fails fast with the exact
package/remedy. Bypass it with ``--skip-preflight`` (the build may then fail
later with a less obvious error).

Common (all stacks)
~~~~~~~~~~~~~~~~~~~~

* **Python 3.10+** and ``west`` (``pip install west pyyaml``)
* **Git** (with SSH access to any private forks a manifest pins, e.g. the
  NavQ95 PX4 fork ``git@github.com:NXP-Robotics/PX4-Autopilot-NXP.git``)
* **CMake** (>= 3.20) and **Ninja**

PX4 targets (NuttX firmware)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Preflight checks for: ``arm-none-eabi-gcc``, ``cmake``, ``ninja``, ``make``,
``git``.

The simplest way to install everything PX4 needs on Ubuntu is PX4's own setup
script (run once, after the sources are cloned):

.. code-block:: console

   bash PX4-Autopilot/Tools/setup/ubuntu.sh

This installs the ARM bare-metal GCC toolchain, build tools, and system
packages. (It uses ``apt`` and therefore needs ``sudo``.)

Zephyr / Cerebri targets
~~~~~~~~~~~~~~~~~~~~~~~~~~

Preflight checks for: ``cmake``, ``ninja``, ``dtc``, ``git``, and attempts to
detect a **Zephyr SDK**.

Install the **Zephyr SDK** and host tools per the upstream Zephyr
getting-started guide, then make the SDK discoverable by exporting one of:

.. code-block:: console

   export ZEPHYR_SDK_INSTALL_DIR=$HOME/zephyr-sdk-<version>
   # or
   export ZEPHYR_TOOLCHAIN_VARIANT=zephyr

If neither is set and no SDK is found in a default location, preflight prints a
warning (not a hard error) because ``west build`` can still auto-discover an
installed SDK.

.. note::

   Preflight installs nothing -- it only verifies. On hosts without
   passwordless ``sudo`` you must install the toolchains out of band using the
   commands above.

Bootstrap the Workspace (Required First Step)
---------------------------------------------

The ``west sdk`` commands are **west extension commands** shipped by this SDK.
They are discovered by west only after a workspace is initialized on this
repository, so you must run ``west init`` **once** before using them:

.. code-block:: console

   west init -m <URL-of-nxp-robotics-sdk> ws
   cd ws

.. note::

   This single ``west init`` is mandatory. ``west sdk download`` and
   ``west sdk build`` do not exist until the workspace is initialized, because
   west only loads extension commands from an initialized manifest.

Download Prebuilt Binaries
--------------------------

Pull NXP prebuilt firmware (and, for NavQ95, the Ubuntu image) from Nexus:

.. code-block:: console

   west sdk download --platform navq95_m7    # firmware + ubuntu .wic
   west sdk download --platform tropic       # firmware only
   west sdk download --platform vmu-rt1176   # firmware only
   west sdk download --platform all          # everything

Artifacts land in ``bin/realtime/`` (and ``ubuntu/`` for NavQ95). Checksums are
verified automatically.

.. note::

   The Nexus backend is not wired up yet. ``download`` currently resolves and
   prints exactly what it *would* fetch (from each platform's
   ``platforms/<platform>/build.yml`` ``download:`` section); the
   transfer itself is stubbed.

Build From Source
-----------------

``west sdk build`` selects the platform/stack manifest, runs ``west update`` to
clone the pinned PX4 / Zephyr / Cerebri sources, then builds the artifacts:

.. code-block:: console

   west sdk build --platform tropic                    # PX4 (default stack)
   west sdk build --platform navq95_m7                 # PX4 (default stack)
   west sdk build --platform navq95_m7 --stack zephyr  # plain Zephyr
   west sdk build --platform vmu-rt1176 --stack cerebri

``--stack`` is optional; each platform has a sensible default.

A toolchain **preflight check** runs first (see :ref:`Prerequisites`); pass
``--skip-preflight`` to bypass it.

Under the hood
--------------

* **PX4** targets shell out to PX4's native ``make <target>``.
* **Zephyr / Cerebri** targets use native ``west build -b <board>``.

In both cases ``west sdk build`` first sets ``manifest.file`` to the chosen
manifest and runs ``west update`` to fetch the pinned sources.
