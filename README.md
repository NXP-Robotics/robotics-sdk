# NXP Robotics SDK

A thin "glue" superproject that unifies multiple robotics platforms across the
**PX4**, **CogniPilot Cerebri**, **Zephyr**, and **AM32** stacks. It owns no
firmware source of its own; instead it pins exact upstream revisions (via west
manifests), dispatches builds to the right build system, and provides prebuilt
artifacts through Nexus.

## Supported platforms and stacks

| Platform       | PX4 | Cerebri | Zephyr | AM32 | Prebuilt artifacts                                   |
|----------------|:---:|:-------:|:------:|:----:|------------------------------------------------------|
| `navq95_m7`    | ✅  |         |   ✅   |      | `nxp_imx95_default.elf/.bin` (+ `.mcuboot.bin`), `nxp_imx95_ddr.elf/.bin` (+ Ubuntu `.wic`) |
| `tropic`       | ✅  |         |   ✅   |      | `nxp_mr-tropic_default.bin`, `nxp_mr-tropic_bootloader.bin` |
| `vmu-rt1176`   | ✅  |   ✅    |   ✅   |      | `nxp_fmurt1176_default.elf/.bin`                     |
| `mr-esc-mcxa`  |     |         |        |  ✅  | `AM32_FRDM_A153_<version>.elf/.bin/.hex`             |

> The Ubuntu WIC image is specific to the NavQ95 A-core and is only available
> for `navq95_m7`. On `navq95_m7`, the PX4 **default (TCM)** image is also
> MCUboot-signed (`nxp_imx95_default.mcuboot.bin`); the DDR image is not.

## Prerequisites

`west sdk build` shells out to the native build systems (PX4's `make`, Zephyr's
`west build`, AM32's `make`), so the required toolchains must **already be
installed** — the SDK manages only the Python layer (a per-workspace venv with
PX4's `requirements.txt`), not system compilers or SDKs. (AM32 is the exception:
it downloads its own xPack ARM toolchain via `make arm_sdk_install`.)

Before building, `west sdk build` runs a **preflight check** that verifies the
required tools and fails fast (with the exact remedy) if any are missing. Pass
`--skip-preflight` to bypass it.

- **Common:** Python 3.10+, `west` (`pip install west pyyaml`), `git` (with SSH
  access to any private forks a manifest pins), `cmake` (>= 3.20), `ninja`.
- **PX4 targets:** ARM bare-metal GCC (`arm-none-eabi-gcc`), `make`. Easiest on
  Ubuntu: `bash PX4-Autopilot/Tools/setup/ubuntu.sh` (after sources are cloned).
- **Zephyr / Cerebri targets:** the **Zephyr SDK** + `dtc`; export
  `ZEPHYR_SDK_INSTALL_DIR` or `ZEPHYR_TOOLCHAIN_VARIANT` so it is discoverable.
- **AM32 targets (`mr-esc-mcxa`):** `make`, `git`, `wget`, `tar`, `python3`. No
  host ARM GCC needed — `make arm_sdk_install` downloads the xPack toolchain
  automatically on first build (needs network).

See `docs/getting_started.rst` for full details. Preflight installs nothing —
on hosts without passwordless `sudo`, install the toolchains out of band.

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

Selects the platform/stack manifest, fetches the pinned sources **when they are
missing** (smart auto-fetch), then builds:

```console
west sdk build --platform tropic                    # PX4 (default stack)
west sdk build --platform navq95_m7                 # PX4 (default stack)
west sdk build --platform navq95_m7 --stack zephyr  # plain Zephyr
west sdk build --platform vmu-rt1176 --stack cerebri
west sdk build --platform mr-esc-mcxa               # AM32 (default stack)
```

`--stack` is optional; each platform has a sensible default (see table).

**Fetching sources.** By default `build` runs `west update` **only when the
manifest's project directories are missing**, so local edits to already-cloned
sources are preserved across rebuilds. Pass `--update` to force a refetch
(resets sources to the manifest revision, **overwriting local changes**). To
fetch/refresh sources without building, use the dedicated verb:

```console
west sdk update --platform tropic                   # clone/update sources only
west sdk build  --platform tropic                   # first build auto-clones; later builds keep your edits
west sdk build  --platform tropic --update          # discard edits, rebuild pinned revision
```

## How it works

- **Manifests** (`manifests/<stack>/<platform>.yml`) pin the exact upstream
  revision for each stack/platform combination.
- **Build definitions** (`platforms/<platform>/build.yml`) are the single
  source of truth for how each platform builds under each stack (kind,
  targets/boards, app, artifacts) and its Nexus `download:` catalog. `sdk.py`
  is a generic dispatcher with no platform-specific knowledge — it discovers
  platforms by scanning `platforms/*/build.yml`.
- **`west sdk build`** sets `manifest.file` to the chosen manifest, runs
- **`west sdk build`** sets `manifest.file` to the chosen manifest, runs
  `west update` (only when sources are missing, or always with `--update`),
  then dispatches on the `build.yml` `kind`:
  - **PX4** → shells out to PX4's native `make <target>`
  - **Zephyr / Cerebri** → native `west build -b <board>`
  - **AM32** → GNU `make <target>` (after a one-time `make arm_sdk_install`
    that downloads the xPack ARM toolchain)
- **`west sdk update`** sets the platform manifest and runs `west update` to
  clone/refresh its sources **without building**.
- **`west sdk download`** reads each platform's `build.yml` `download:` section
  and pulls the matching prebuilt artifacts from Nexus (WIC included only for
  `navq95_m7`).

## Repository layout

```
nxp-robotics-sdk/
├── manifests/{px4,cerebri,zephyr,am32}/<platform>.yml  # revision pinning
├── platforms/<platform>/build.yml                  # build + download catalog
├── scripts/
│   ├── sdk.py                # `west sdk download` + `west sdk build`
│   └── west-commands.yml      # registers the `sdk` command
└── docs/                      # Sphinx (reStructuredText) documentation
```

Prebuilt binaries and the Ubuntu WIC are **not** committed to git; they are
hosted on Nexus and retrieved with `west sdk download`.
