# NXP Robotics SDK

A thin "glue" superproject that unifies multiple robotics platforms across the
**PX4**, **CogniPilot Cerebri**, and **Zephyr** stacks. It owns no firmware
source of its own; instead it pins exact upstream revisions (via west
manifests), dispatches builds to the right build system, and provides prebuilt
artifacts through Nexus.

## Supported platforms and stacks

| Platform      | PX4 | Cerebri | Zephyr | Prebuilt artifacts                                   |
|---------------|:---:|:-------:|:------:|------------------------------------------------------|
| `navq95_m7`   |     |   ✅    |   ✅   | `navq95.elf/.bin`, `navq95_ddr.elf/.bin` (+ Ubuntu `.wic`) |
| `tropic`      | ✅  |         |   ✅   | `mr-tropic.bin`, `mr-tropic.bootloader.bin`          |
| `vmu-rt1176`  | ✅  |   ✅    |   ✅   | `vmu-rt1176.elf/.bin`                                |

> The Ubuntu WIC image is specific to the NavQ95 A-core and is only available
> for `navq95_m7`.

## Prerequisites

- Python 3.10+ and `west` (`pip install west pyyaml`)
- A host toolchain:
  - **Zephyr SDK** for Zephyr / Cerebri targets
  - **ARM GCC** for PX4 targets

## Quick Start

### 1. Bootstrap the workspace (one-time, REQUIRED)

All `west sdk ...` commands are **west extension commands** shipped by this SDK.
West only discovers them **after** a workspace has been initialized on this
repository. Run this once:

```console
west init -m <URL-of-nxp-robotics-sdk> ws
cd ws
```

> Without this step, `west sdk download` and `west sdk build` do not exist yet.

### 2a. Download prebuilt binaries (no build)

Pulls NXP prebuilt firmware — and, for NavQ95, the Ubuntu image — from Nexus:

```console
west sdk download --platform navq95_m7    # firmware + ubuntu .wic
west sdk download --platform tropic       # firmware only
west sdk download --platform vmu-rt1176   # firmware only
west sdk download --platform all          # everything
```

Artifacts land in `bin/realtime/` (and `ubuntu/` for NavQ95); checksums are
verified.

> **Status:** the Nexus backend is not wired up yet. `download` currently
> resolves and prints exactly what it *would* fetch (from each platform's
> `platforms/<platform>/build.yml` `download:` section); the transfer itself
> is a stub. See the TODO in `scripts/sdk.py`.

### 2b. Build from source

Selects the platform/stack manifest, clones the pinned sources
(`west update`), then builds:

```console
west sdk build --platform tropic                    # PX4 (default stack)
west sdk build --platform navq95_m7                 # PX4 (default stack)
west sdk build --platform navq95_m7 --stack zephyr  # plain Zephyr
west sdk build --platform vmu-rt1176 --stack cerebri
```

`--stack` is optional; each platform has a sensible default (see table).

## How it works

- **Manifests** (`manifests/<stack>/<platform>.yml`) pin the exact upstream
  revision for each stack/platform combination.
- **Build definitions** (`platforms/<platform>/build.yml`) are the single
  source of truth for how each platform builds under each stack (kind,
  targets/boards, app, artifacts) and its Nexus `download:` catalog. `sdk.py`
  is a generic dispatcher with no platform-specific knowledge — it discovers
  platforms by scanning `platforms/*/build.yml`.
- **`west sdk build`** sets `manifest.file` to the chosen manifest, runs
  `west update`, then dispatches on the `build.yml` `kind`:
  - **PX4** → shells out to PX4's native `make <target>`
  - **Zephyr / Cerebri** → native `west build -b <board>`
- **`west sdk download`** reads each platform's `build.yml` `download:` section
  and pulls the matching prebuilt artifacts from Nexus (WIC included only for
  `navq95_m7`).

## Repository layout

```
nxp-robotics-sdk/
├── manifests/{px4,cerebri,zephyr}/<platform>.yml   # revision pinning
├── platforms/<platform>/build.yml                  # build + download catalog
├── scripts/
│   ├── sdk.py                # `west sdk download` + `west sdk build`
│   └── west-commands.yml      # registers the `sdk` command
└── docs/                      # Sphinx (reStructuredText) documentation
```

Prebuilt binaries and the Ubuntu WIC are **not** committed to git; they are
hosted on Nexus and retrieved with `west sdk download`.
