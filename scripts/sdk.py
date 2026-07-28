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
            "px4": {
                "kind": "px4",
                # PX4 make targets to build, in order (app first, bootloader next).
                "targets": ["nxp_mr-tropic_default", "nxp_mr-tropic_bootloader"],
                # PX4 build outputs to collect into bin/realtime/. Names are kept
                # identical to PX4's output; rename upstream in PX4 if needed.
                "artifacts": [
                    "nxp_mr-tropic_default/nxp_mr-tropic_default.bin",
                    "nxp_mr-tropic_bootloader/nxp_mr-tropic_bootloader.bin",
                ],
            },
            "zephyr": {"kind": "zephyr", "board": "mr_tropic", "app": None},
        },
        "default_stack": "px4",
    },
    "vmu-rt1176": {
        "stacks": {
            "px4": {
                "kind": "px4",
                "targets": ["nxp_fmurt1176_default"],
                "artifacts": [
                    "nxp_fmurt1176_default/nxp_fmurt1176_default.elf",
                    "nxp_fmurt1176_default/nxp_fmurt1176_default.bin",
                ],
            },
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
            self._build_px4(cfg, args.jobs)
        elif cfg["kind"] == "zephyr":
            self._build_zephyr(cfg["board"], cfg.get("app"))
        else:  # pragma: no cover
            log.die(f"Unknown build kind: {cfg['kind']}")

    def _build_px4(self, cfg, jobs):
        px4_dir = os.path.join(self.topdir, "PX4-Autopilot")
        if not os.path.isdir(px4_dir):
            log.die(f"PX4-Autopilot not found at {px4_dir}. "
                    f"Did 'west update' run?")
        # Ensure PX4's Python dependencies are available in an isolated venv,
        # then build with that venv on PATH so `make` picks up `em`, jinja2, etc.
        env = self._ensure_px4_venv(px4_dir)
        # PX4 (and its nested cyclonedds/idlc host build) use `ccache` as a
        # compiler launcher. If real ccache is not installed and cannot be
        # installed (no sudo), provide a passthrough shim in the venv bin so the
        # nested builds -- which ignore USE_CCACHE -- still find a `ccache`.
        self._ensure_ccache(env)
        targets = cfg.get("targets") or [cfg["target"]]
        for target in targets:
            log.inf(f"PX4 build: make {target} -j{jobs}")
            self._run(["make", target, f"-j{jobs}"], cwd=px4_dir, env=env)

        # Collect built firmware into <topdir>/bin/realtime/, keeping the exact
        # PX4 output filenames (no renaming here).
        self._collect_artifacts(
            src_root=os.path.join(px4_dir, "build"),
            artifacts=cfg.get("artifacts", []),
        )

    def _collect_artifacts(self, src_root, artifacts):
        """Copy built outputs to <topdir>/bin/realtime/, preserving filenames.

        `artifacts` is a list of paths relative to `src_root`. The basename of
        each is kept as-is; rename upstream in PX4 if a different name is wanted.
        """
        import shutil
        if not artifacts:
            log.wrn("No artifacts defined for this target; nothing to collect.")
            return
        dest_dir = os.path.join(self.topdir, "bin", "realtime")
        os.makedirs(dest_dir, exist_ok=True)
        for rel_src in artifacts:
            src = os.path.join(src_root, rel_src)
            name = os.path.basename(rel_src)
            dst = os.path.join(dest_dir, name)
            if not os.path.isfile(src):
                log.die(f"Expected build output not found: {src}")
            shutil.copy2(src, dst)
            log.inf(f"Collected: {name}  ->  bin/realtime/")

    def _ensure_ccache(self, env):
        """Guarantee a usable `ccache` on PATH for the build.

        If real ccache exists, do nothing. Otherwise install a passthrough shim
        into the venv's bin/ (already on PATH) that simply execs its arguments,
        so build systems that hardcode ccache as a launcher keep working.
        """
        import shutil, stat
        if shutil.which("ccache", path=env.get("PATH")):
            return
        venv_bin = os.path.join(env["VIRTUAL_ENV"], "bin")
        os.makedirs(venv_bin, exist_ok=True)
        shim = os.path.join(venv_bin, "ccache")
        if not os.path.exists(shim):
            log.inf(f"ccache not available; installing passthrough shim: {shim}")
            with open(shim, "w") as f:
                f.write('#!/bin/sh\n'
                        '# Passthrough ccache shim (no caching) added by the\n'
                        '# NXP Robotics SDK because real ccache is unavailable.\n'
                        'exec "$@"\n')
            os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC |
                     stat.S_IXGRP | stat.S_IXOTH)

    def _ensure_px4_venv(self, px4_dir):
        """Create (once) a venv with PX4's Python requirements and return an
        environment dict with that venv activated (venv/bin prepended to PATH,
        VIRTUAL_ENV set). Idempotent via a stamp file keyed on the requirements
        file contents.
        """
        import hashlib

        venv_dir = os.path.join(self.topdir, ".px4-venv")
        req = os.path.join(px4_dir, "Tools", "setup", "requirements.txt")
        if not os.path.exists(req):
            # Older/newer PX4 layouts: fall back to a top-level requirements.txt.
            alt = os.path.join(px4_dir, "requirements.txt")
            req = alt if os.path.exists(alt) else req

        venv_py = os.path.join(venv_dir, "bin", "python")

        # Build the activated environment up-front (used whether or not we
        # (re)install below).
        env = dict(os.environ)
        env["VIRTUAL_ENV"] = venv_dir
        env["PATH"] = os.path.join(venv_dir, "bin") + os.pathsep + env.get("PATH", "")
        # Avoid the user site-packages leaking in / out.
        env.pop("PYTHONHOME", None)

        # Compute a stamp from the requirements file so we reinstall only when
        # requirements change.
        stamp = os.path.join(venv_dir, ".requirements.sha256")
        want = ""
        if os.path.exists(req):
            with open(req, "rb") as f:
                want = hashlib.sha256(f.read()).hexdigest()

        have = ""
        if os.path.exists(stamp):
            with open(stamp) as f:
                have = f.read().strip()

        if os.path.isfile(venv_py) and want and want == have:
            log.inf(f"PX4 venv up to date: {venv_dir}")
            return env

        if not os.path.isfile(venv_py):
            log.inf(f"Creating PX4 venv: {venv_dir}")
            self._run([sys.executable, "-m", "venv", venv_dir])

        # Upgrade pip tooling, then install PX4 requirements into the venv.
        self._run([venv_py, "-m", "pip", "install", "--upgrade",
                   "pip", "setuptools", "wheel"])
        if os.path.exists(req):
            log.inf(f"Installing PX4 requirements: {req}")
            self._run([venv_py, "-m", "pip", "install", "-r", req])
            with open(stamp, "w") as f:
                f.write(want)
        else:
            log.wrn(f"PX4 requirements file not found ({req}); "
                    f"venv created but no packages installed.")
        return env

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

    def _run(self, cmd, cwd=None, env=None):
        log.dbg(f"$ {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
        try:
            subprocess.check_call(cmd, cwd=cwd, env=env)
        except FileNotFoundError:
            log.die(f"Command not found: {cmd[0]}")
        except subprocess.CalledProcessError as e:
            log.die(f"Command failed ({e.returncode}): {' '.join(cmd)}")
