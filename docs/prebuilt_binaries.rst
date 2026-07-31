Prebuilt Binaries
=================

Prebuilt firmware for every platform -- and the NavQ95 Ubuntu image -- are
hosted on Nexus, **not** committed to git. Retrieve them with ``west sdk
download`` after the one-time workspace bootstrap (see :doc:`getting_started`).

.. code-block:: console

   west sdk download --platform navq95    # firmware + ubuntu .wic
   west sdk download --platform tropic       # firmware only
   west sdk download --platform vmu-rt1176   # firmware only
   west sdk download --platform all          # everything

Artifacts are placed in:

* ``bin/<platform>/``          -- firmware binaries (tropic, vmu-rt1176, mr-esc-mcxa)
* ``bin/navq95/realtime/``  -- NavQ95 M7 firmware binaries
* ``bin/navq95/ubuntu/``    -- NavQ95 Ubuntu WIC image (``navq95`` only)

The artifact catalog is defined per platform in the ``download:`` section of
``platforms/<platform>/build.yml``.

.. note::

   The Nexus backend is not wired up yet. ``download`` currently resolves and
   prints exactly what it would fetch; the transfer and checksum verification
   are stubbed pending the Nexus repository.
