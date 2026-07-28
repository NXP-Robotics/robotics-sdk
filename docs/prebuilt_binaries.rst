Prebuilt Binaries
=================

Prebuilt firmware for every platform -- and the NavQ95 Ubuntu image -- are
hosted on Nexus, **not** committed to git. Retrieve them with ``west sdk
download`` after the one-time workspace bootstrap (see :doc:`getting_started`).

.. code-block:: console

   west sdk download --platform navq95_m7    # firmware + ubuntu .wic
   west sdk download --platform tropic       # firmware only
   west sdk download --platform vmu-rt1176   # firmware only
   west sdk download --platform all          # everything

Artifacts are placed in:

* ``bin/realtime/`` -- firmware binaries (all platforms)
* ``ubuntu/``       -- NavQ95 Ubuntu WIC image (``navq95_m7`` only)

The artifact catalog is defined in ``scripts/artifacts.yml``.

.. note::

   The Nexus backend is not wired up yet. ``download`` currently resolves and
   prints exactly what it would fetch; the transfer and checksum verification
   are stubbed pending the Nexus repository.
