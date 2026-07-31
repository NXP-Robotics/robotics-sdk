Release Notes
=============

2026.07.0
---------

* Initial NXP Robotics SDK superproject scaffold.
* Platforms: ``navq95`` (i.MX95 M7), ``tropic`` (mr-tropic),
  ``vmu-rt1176`` (CogniPilot VMU on RT1176), ``mr-esc-mcxa`` (AM32 ESC on
  FRDM-MCXA153).
* Stacks: PX4, CogniPilot Cerebri, plain Zephyr, AM32 (selectable per
  platform).
* ``west sdk build`` -- manifest select + smart source fetch + build dispatch
  (PX4/AM32 ``make`` shell-out / native Zephyr ``west build``). Sources are
  fetched via ``west update`` only when missing, preserving local edits across
  rebuilds; ``--update`` forces a refetch to the manifest revision.
* ``west sdk update`` -- set the platform manifest and fetch/refresh its
  sources (``west update``) without building.
* ``west sdk download`` -- prebuilt artifact retrieval from Nexus
  (backend stubbed; per-platform catalog in ``platforms/<platform>/build.yml``).
* Per-platform build configuration lives in ``platforms/<platform>/build.yml``;
  ``scripts/sdk.py`` is a generic loader/dispatcher that discovers platforms by
  scanning ``platforms/*/build.yml`` (no platform-specific logic).

.. todo::

   Pin exact upstream revisions in ``manifests/`` and wire the Nexus backend
   (real repo URL + sha256 checksums) before the first customer release.
