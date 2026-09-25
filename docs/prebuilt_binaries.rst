Prebuilt Binaries
=================

Prebuilt firmware for every platform -- and the NavQ95 Linux image -- are
fetched online, **not** committed to git. Retrieve them with ``west sdk
download`` after the one-time workspace bootstrap (see :doc:`getting_started`).

.. code-block:: console

   west sdk download --platform mr-navq95            # M7 firmware + NavQ95 Linux .wic image
   west sdk download --platform mr-vmu-tropic        # firmware only
   west sdk download --platform mr-esc-am32-mcxa133  # firmware only
   west sdk download --platform all                  # everything

Artifacts are placed in:

* ``bin/<platform>/``            -- firmware binaries (mr-vmu-tropic, mr-esc-am32-mcxa133)
* ``bin/mr-navq95/realtime/``    -- NavQ95 M7 firmware binaries
* ``bin/mr-navq95/linux/``       -- NavQ95 A-core Linux ``.wic.zst`` image + ``.wic.bmap``

Each platform's ``platforms/<platform>/build.yml`` ``download:`` section
selects which files to fetch and references an artifact *class* (``mcu`` /
``mpu``). The base URL of each class lives in one authoritative place: the
root ``versions.yml``. Files are fetched directly over HTTPS from
``<base_url>/<subpath>/<filename>`` -- there is no listing step. Use
``--dry-run`` to print exactly what would be fetched (URLs and destinations)
without downloading.

Sources
-------

The root ``versions.yml`` is the source of truth: it defines a single base
URL per artifact class::

   artifacts:
     mcu:
       base_url: <mcu firmware base URL>
     mpu:
       base_url: <mpu / Linux image base URL>

To publish the binaries from a different location, change only these two
URLs. A platform may override a whole class for itself via a top-level
``artifacts:`` block in ``platforms/<platform>/build.yml``.

Some hosts require credentials; these are read from ``~/.netrc``
automatically (which must be ``chmod 600``)::

   machine <host> login <user> password <secret>

Integrity
---------

Transfers are protected by HTTPS and written atomically (a partial transfer
is discarded, never left in place). The NavQ95 ``.wic.zst`` image is left
**compressed** and is accompanied by its ``.wic.bmap`` for fast flashing with
``bmaptool``.
