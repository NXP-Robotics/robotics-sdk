Prebuilt Binaries
=================

Prebuilt firmware for every platform -- and the NavQ95 Linux image -- are
hosted on Nexus, **not** committed to git. Retrieve them with ``west sdk
download`` after the one-time workspace bootstrap (see :doc:`getting_started`).

.. code-block:: console

   west sdk download --platform navq95       # M7 firmware + NavQ95 Linux .wic image
   west sdk download --platform tropic       # firmware only
   west sdk download --platform vmu-rt1176   # firmware only
   west sdk download --platform mr-esc-mcxa  # firmware only
   west sdk download --platform all          # everything

Artifacts are placed in:

* ``bin/<platform>/``          -- firmware binaries (tropic, vmu-rt1176, mr-esc-mcxa)
* ``bin/navq95/realtime/``  -- NavQ95 M7 firmware binaries
* ``bin/navq95/ubuntu/``    -- NavQ95 A-core Linux ``.wic.zst`` image + ``.wic.bmap`` (``navq95`` only)

Each platform's ``platforms/<platform>/build.yml`` ``download:`` section selects
which files to fetch and references an artifact *class* (``mcu`` / ``mpu``). The
store location and the pinned version of each class live in one authoritative
place: the root ``versions.yml``.

Authentication (User Token)
---------------------------

The binaries live in an internal artifact store (currently the Nexus RAW
repository named in ``versions.yml``). Access requires a **User Token**, stored
in ``~/.netrc``:

#. Log in to the store (the ``base_url`` in ``versions.yml``).
#. Top-right (your username) -> **User Token** -> **Access User Token**.
#. Copy the **Name Code** and **Pass Code**.

Add them to ``~/.netrc`` (which must be ``chmod 600``)::

   machine <store-host> login <NameCode> password <PassCode>

If ``~/.netrc`` has no entry for the store host, ``west sdk download`` prompts
for the Name/Pass codes interactively on first use and appends them to
``~/.netrc`` (``chmod 600``) for subsequent runs. The token is never printed
or logged.

Versions
--------

The root ``versions.yml`` is the default source of truth: it defines each
artifact class (its store location and pinned build). Each class is
self-contained::

   artifacts:
     mcu:
       base_url: https://nl4-nxrm.sw.nxp.com
       repository: TARS_Daily_Build
       path: Robotics_Firmware
       version: latest
     mpu:
       base_url: https://nl4-nxrm.sw.nxp.com
       repository: TARS_Daily_Build
       path: Robotics_Release
       version: latest

Daily builds track ``latest`` for both classes. A release pins each class to
its own build number (the MCU firmware and the MPU/Linux image come from
separate pipelines with independent numbering, e.g. ``mcu: 35`` /
``mpu: 20``).

A platform may override a whole class for itself: a top-level ``artifacts:``
block in ``platforms/<platform>/build.yml`` that defines a class replaces the
generic one from ``versions.yml`` for that platform (e.g. to pull a class from
a different store). Use ``--dry-run`` to resolve and print exactly what would
be fetched (URLs, sizes, checksums) without downloading.

Integrity
---------

Every artifact is verified after transfer against the size and SHA-256 reported
by the store (or an explicit ``sha256`` pinned in a ``download:`` entry). A
mismatch aborts the download and removes the partial file. The NavQ95
``.wic.zst`` image is left **compressed** and is accompanied by its ``.wic.bmap``
for fast flashing with ``bmaptool``.
