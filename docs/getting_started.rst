Getting Started
===============

Prerequisites
-------------

* Python 3.10+ and ``west`` (``pip install west pyyaml``)
* A host toolchain:

  * **Zephyr SDK** for Zephyr / Cerebri targets
  * **ARM GCC** for PX4 targets

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
   west sdk build --platform navq95_m7                 # Cerebri (default stack)
   west sdk build --platform navq95_m7 --stack zephyr  # plain Zephyr
   west sdk build --platform vmu-rt1176 --stack cerebri

``--stack`` is optional; each platform has a sensible default.

Under the hood
--------------

* **PX4** targets shell out to PX4's native ``make <target>``.
* **Zephyr / Cerebri** targets use native ``west build -b <board>``.

In both cases ``west sdk build`` first sets ``manifest.file`` to the chosen
manifest and runs ``west update`` to fetch the pinned sources.
