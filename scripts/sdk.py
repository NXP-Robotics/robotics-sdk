"""NXP Robotics SDK west extension commands: ``download`` and ``build``.

These are exposed under a single ``sdk`` command group so the customer uses a
uniform, stack-agnostic interface:

    west sdk download --platform <platform>
    west sdk build    --platform <platform> [--stack <stack>]

Both commands require a west workspace to already exist (i.e. the customer must
have run ``west init -m <sdk-url> ws`` once), because west only discovers these
extension commands after a workspace is initialized.

NOTE: The Nexus download backend is not wired yet. ``download`` resolves the
artifact list from scripts/artifacts.yml and prints exactly what it *would*
fetch; the actual transfer is a clearly marked TODO.
"""

import os
import subprocess
import sys

from west import log
from west.commands import WestCommand

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------------- #
# Registry: which stacks are valid per platform, and how to build each combo.
#
# For PX4 platforms we store the PX4 make target.
# For Zephyr/Cerebri platforms we store the Zephyr board name (+ optional app).
# --------------------------------------------------------------------------- #
PLATFORMS = {
    "navq95_m7": {
        "stacks": {
            "cerebri": {"kind": "zephyr", "board": "navq95_m7", "app": "app/cerebri"},
            "zephyr":  {"kind": "zephyr", "board": "navq95_m7", "app": None},
        },
        "default_stack": "cerebri",
    },
    "tropic": {
        "stacks": {
            "px4":    {"kind": "px4", "target": "nxp_mr-tropic_default"},
            "zephyr": {"kind": "zephyr", "board": "mr_tropic", "app": None},
        },
        "default_stack": "px4",
    },
    "vmu-rt1176": {
        "stacks": {
            "px4":     {"kind": "px4", "target": "nxp_fmurt1176_default"},
            "cerebri": {"kind": "zephyr", "board": "vmu_rt1176", "app": "app/cerebri"},
            "zephyr":  {"kind": "zephyr", "board": "vmu_rt1176", "app": None},
        },
        "default_stack": "px4",
    },
}

# Manifest file for a given (stack, platform), relative to the SDK repo root.
def manifest_for(stack, platform):
    return os.path.join("manifests", stack, f"{platform}.yml")


def all_platforms():
    return list(PLATFORMS.keys())


def resolve_stack(platform, stack):
    """Return the validated stack name for a platform, or exit with an error."""
    entry = PLATFORMS[platform]
    if stack is None:
        stack = entry["default_stack"]
    if stack not in entry["stacks"]:
        valid = ", ".join(entry["stacks"])
        log.die(f"Stack '{stack}' is not valid for platform '{platform}'. "
                f"Valid stacks: {valid}")
    return stack


class Sdk(WestCommand):
    """Parent command; delegates to the ``download`` / ``build`` subcommands."""

    def __init__(self):
        super().__init__(
            "sdk",
            "NXP Robotics SDK: download prebuilt artifacts or build from source",
            "Download prebuilt binaries from Nexus, or build firmware from "
            "pinned sources, for a supported platform.",
        )

    def do_add_parser(self, parser_adder):
        parser = parser_adder.add_parser(self.name, help=self.help)
        sub = parser.add_subparsers(dest="action", metavar="<action>")
        sub.required = True

        # -- download --
        dl = sub.add_parser(
            "download",
            help="Download prebuilt artifacts (firmware, and Ubuntu WIC for navq95) from Nexus",
        )
        dl.add_argument(
            "-p", "--platform", required=True,
            choices=all_platforms() + ["all"],
            help="Platform to download artifacts for, or 'all'.",
        )

        # -- build --
        bd = sub.add_parser(
            "build",
            help="Init manifest, clone pinned sources (west update), then build",
        )
        bd.add_argument(
            "-p", "--platform", required=True, choices=all_platforms(),
            help="Platform to build.",
        )
        bd.add_argument(
            "-s", "--stack", default=None,
            help="Stack to build (px4 | cerebri | zephyr). Defaults to the "
                 "platform's default stack.",
        )
        bd.add_argument(
            "-j", "--jobs", default=str(os.cpu_count() or 1),
            help="Parallel build jobs (PX4 make -j).",
        )
        bd.add_argument(
            "--no-update", action="store_true",
            help="Skip 'west update' (assume sources already cloned).",
        )
        return parser

    def do_run(self, args, unknown):
        if args.action == "download":
            self._do_download(args)
        elif args.action == "build":
            self._do_build(args)
        else:  # pragma: no cover - argparse enforces this
            log.die(f"Unknown action: {args.action}")

    # ----------------------------------------------------------------------- #
    # download
    # ----------------------------------------------------------------------- #
    def _load_artifacts(self):
        path = os.path.join(self.topdir, "nxp-robotics-sdk",
                            "scripts", "artifacts.yml")
        if not os.path.exists(path):
            # Fallback: SDK repo may be the topdir itself (rare) or a sibling.
            alt = os.path.join(os.path.dirname(__file__), "artifacts.yml")
            path = alt if os.path.exists(alt) else path
        if yaml is None:
            log.die("PyYAML is required. Install with: pip install pyyaml")
        if not os.path.exists(path):
            log.die(f"artifacts manifest not found: {path}")
        with open(path) as f:
            return yaml.safe_load(f), path

    def _do_download(self, args):
        data, path = self._load_artifacts()
        platforms = all_platforms() if args.platform == "all" else [args.platform]

        repo = data.get("repo", "<unset>")
        version = data.get("version", "<unset>")
        catalog = data.get("platforms", {})

        log.inf(f"Nexus repo   : {repo}")
        log.inf(f"SDK version  : {version}")
        log.inf(f"Artifact map : {path}")
        log.inf("")

        total = 0
        for plat in platforms:
            items = catalog.get(plat, [])
            if not items:
                log.wrn(f"[{plat}] no artifacts declared in artifacts.yml")
                continue
            log.inf(f"[{plat}] {len(items)} artifact(s):")
            for a in items:
                name = a.get("name")
                dest = a.get("dest", "bin/realtime/")
                dest_path = os.path.join(self.topdir, dest, name)
                log.inf(f"    - {name}  ->  {dest}")
                # TODO(nexus): replace this stub with the real Nexus fetch:
                #   1. GET {repo}/{version}/{plat}/{name}
                #   2. write to dest_path
                #   3. verify sha256 against a['sha256']
                self._download_stub(repo, version, plat, name, dest_path,
                                    a.get("sha256"))
                total += 1
            log.inf("")

        log.inf(f"Planned {total} download(s). "
                f"NOTE: Nexus backend not yet implemented (stubbed).")

    def _download_stub(self, repo, version, platform, name, dest_path, sha256):
        # Placeholder until Nexus is available. Intentionally does NOT create
        # files, so nothing looks 'downloaded' when it was not.
        url = f"{repo.rstrip('/')}/{version}/{platform}/{name}"
        log.dbg(f"      would fetch: {url}")
        log.dbg(f"      would write: {dest_path}")
        log.dbg(f"      would verify sha256: {sha256}")

    # ----------------------------------------------------------------------- #
    # build
    # ----------------------------------------------------------------------- #
    def _do_build(self, args):
        platform = args.platform
        stack = resolve_stack(platform, args.stack)
        cfg = PLATFORMS[platform]["stacks"][stack]
        mf = manifest_for(stack, platform)

        log.inf(f"Platform : {platform}")
        log.inf(f"Stack    : {stack}")
        log.inf(f"Manifest : {mf}")

        # 1. Point the workspace at the platform/stack manifest.
        self._run(self._west("config", "manifest.path", "nxp-robotics-sdk"))
        self._run(self._west("config", "manifest.file", mf))

        # 2. Clone / sync the pinned sources.
        if args.no_update:
            log.inf("Skipping 'west update' (--no-update).")
        else:
            log.inf("Cloning pinned sources: west update ...")
            self._run(self._west("update"))

        # 3. Dispatch to the correct build system.
        if cfg["kind"] == "px4":
            self._build_px4(cfg["target"], args.jobs)
        elif cfg["kind"] == "zephyr":
            self._build_zephyr(cfg["board"], cfg.get("app"))
        else:  # pragma: no cover
            log.die(f"Unknown build kind: {cfg['kind']}")

    def _build_px4(self, target, jobs):
        px4_dir = os.path.join(self.topdir, "PX4-Autopilot")
        if not os.path.isdir(px4_dir):
            log.die(f"PX4-Autopilot not found at {px4_dir}. "
                    f"Did 'west update' run?")
        log.inf(f"PX4 build: make {target} -j{jobs}")
        self._run(["make", target, f"-j{jobs}"], cwd=px4_dir)

    def _build_zephyr(self, board, app):
        cmd = self._west("build", "-b", board)
        if app:
            cmd.append(app)
        log.inf(f"Zephyr build: {' '.join(cmd)}")
        self._run(cmd)

    # ----------------------------------------------------------------------- #
    # helpers
    # ----------------------------------------------------------------------- #
    def _west(self, *args):
        """Invoke west portably.

        Prefer a bare 'west' executable if it is on PATH; otherwise fall back
        to 'python3 -m west' (some hosts install west as a module only).
        """
        import shutil
        if shutil.which("west"):
            return ["west", *args]
        return [sys.executable, "-m", "west", *args]

    def _run(self, cmd, cwd=None):
        log.dbg(f"$ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
        try:
            subprocess.check_call(cmd, cwd=cwd)
        except FileNotFoundError:
            log.die(f"Command not found: {cmd[0]}")
        except subprocess.CalledProcessError as e:
            log.die(f"Command failed ({e.returncode}): {' '.join(cmd)}")
