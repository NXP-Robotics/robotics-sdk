"""NXP Robotics SDK west extension commands: ``download`` and ``build``.

Uniform, stack-agnostic interface:

    west sdk download --platform <platform> | all
    west sdk build    --platform <platform> [--stack <stack>]

Both require an initialized west workspace (run ``west init -l nxp-robotics-sdk``
once) because west only discovers extension commands after init.

sdk.py contains NO platform-specific knowledge. Each platform owns a single
definition file ``platforms/<platform>/build.yml`` describing its stacks
(kind, targets/boards, app, artifacts) and its ``download`` catalog.

NOTE: The Nexus download backend is not wired yet; ``download`` resolves the
catalog and prints what it *would* fetch (clearly marked TODO).
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
# Generic platform discovery. Each platform owns platforms/<platform>/build.yml.
# --------------------------------------------------------------------------- #
SDK_REPO = "nxp-robotics-sdk"

# Nexus location for prebuilt artifacts (download). TODO: wire real backend.
NEXUS_REPO = "https://nexus.example.nxp.com/repository/nxp-robotics-sdk-raw"


def _sdk_root(topdir):
    return os.path.join(topdir, SDK_REPO)


def platforms_dir(topdir):
    return os.path.join(_sdk_root(topdir), "platforms")


def all_platforms(topdir):
    """Discover platforms by scanning platforms/<name>/build.yml."""
    root = platforms_dir(topdir)
    if not os.path.isdir(root):
        return []
    return sorted(
        name for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name, "build.yml"))
    )


def load_platform(topdir, platform):
    """Load and return (data, path) for a platform's build.yml."""
    if yaml is None:
        log.die("PyYAML is required. Install with: pip install pyyaml")
    path = os.path.join(platforms_dir(topdir), platform, "build.yml")
    if not os.path.isfile(path):
        log.die(f"Unknown platform '{platform}': {path} not found.")
    with open(path) as f:
        return (yaml.safe_load(f) or {}), path


def resolve_stack(pdata, platform, stack):
    """Return the validated stack name for a platform, or exit with an error."""
    stacks = pdata.get("stacks", {})
    if stack is None:
        stack = pdata.get("default_stack")
        if stack is None:
            log.die(f"Platform '{platform}' defines no default_stack; "
                    f"pass --stack explicitly.")
    if stack not in stacks:
        valid = ", ".join(stacks) or "(none)"
        log.die(f"Stack '{stack}' is not valid for platform '{platform}'. "
                f"Valid stacks: {valid}")
    return stack


def manifest_for(stack, platform):
    """Manifest path (relative to SDK repo root) for a (stack, platform)."""
    return os.path.join("manifests", stack, f"{platform}.yml")


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

        dl = sub.add_parser(
            "download",
            help="Download prebuilt artifacts (firmware, and Ubuntu WIC for navq95) from Nexus",
        )
        dl.add_argument("-p", "--platform", required=True,
                        help="Platform to download for, or 'all'.")

        bd = sub.add_parser(
            "build",
            help="Init manifest, clone pinned sources (west update), then build",
        )
        bd.add_argument("-p", "--platform", required=True, help="Platform to build.")
        bd.add_argument("-s", "--stack", default=None,
                        help="Stack to build. Defaults to the platform's default_stack.")
        bd.add_argument("-j", "--jobs", default=str(os.cpu_count() or 1),
                        help="Parallel build jobs.")
        bd.add_argument("--no-update", action="store_true",
                        help="Skip 'west update' (assume sources already cloned).")
        bd.add_argument("--skip-preflight", action="store_true",
                        help="Skip the toolchain/dependency preflight checks.")
        return parser

    def do_run(self, args, unknown):
        if args.action == "download":
            self._do_download(args)
        elif args.action == "build":
            self._do_build(args)
        else:  # pragma: no cover
            log.die(f"Unknown action: {args.action}")

    # ------------------------------------------------------------------ #
    # download
    # ------------------------------------------------------------------ #
    def _do_download(self, args):
        if args.platform == "all":
            targets = all_platforms(self.topdir)
            if not targets:
                log.die("No platforms discovered under platforms/*/build.yml.")
        else:
            targets = [args.platform]

        for platform in targets:
            pdata, _ = load_platform(self.topdir, platform)
            entries = pdata.get("download", []) or []
            if not entries:
                log.wrn(f"[{platform}] no download entries defined; skipping.")
                continue
            log.inf(f"=== download: {platform} ===")
            for entry in entries:
                self._download_stub(platform, entry)

    def _download_stub(self, platform, entry):
        """Print what would be fetched. Creates NO files (backend not wired)."""
        name = entry.get("name", "<unnamed>")
        src = entry.get("src") or name
        dest = entry.get("dest", os.path.join("bin", "realtime", ""))
        # A dest ending in '/' (or a bare dir) is a destination directory.
        if dest.endswith("/") or dest.endswith(os.sep) or not os.path.splitext(dest)[1]:
            dest = os.path.join(dest, name)
        url = f"{NEXUS_REPO}/{platform}/{src}"
        log.inf(f"  - {name}")
        log.inf(f"    TODO would fetch : {url}")
        log.inf(f"    TODO would write : {os.path.join(self.topdir, dest)}")
        if entry.get("sha256"):
            log.inf(f"    TODO would verify: sha256={entry['sha256']}")

    # ------------------------------------------------------------------ #
    # build
    # ------------------------------------------------------------------ #
    def _do_build(self, args):
        pdata, ppath = load_platform(self.topdir, args.platform)
        stack = resolve_stack(pdata, args.platform, args.stack)
        cfg = dict(pdata["stacks"][stack])
        kind = cfg.get("kind")
        if not kind:
            log.die(f"Stack '{stack}' in {ppath} has no 'kind'.")

        log.inf(f"=== build: platform={args.platform} stack={stack} kind={kind} ===")

        # Fail fast on a clean system: verify the required toolchain/system
        # dependencies exist before cloning or building anything.
        if not args.skip_preflight:
            self._preflight(kind)
        else:
            log.wrn("Skipping preflight checks (--skip-preflight).")

        # Point the workspace manifest at the (stack, platform) file, update,
        # then build. The platform manifests use ``import: true``; if left
        # active after the build, west can no longer discover this 'sdk'
        # extension command whenever the imported sources are absent. So we
        # always restore manifest.file to the import-free bootstrap west.yml
        # afterwards, leaving the workspace in a usable state.
        manifest_rel = manifest_for(stack, args.platform)
        self._set_manifest(manifest_rel)
        try:
            if not args.no_update:
                self._west(["update"], cwd=self.topdir)
            else:
                log.inf("Skipping 'west update' (--no-update).")

            if kind == "px4":
                self._build_px4(cfg, args.jobs)
            elif kind in ("zephyr", "cerebri"):
                self._build_zephyr(cfg, args.jobs)
            else:
                log.die(f"Unsupported build kind '{kind}'.")
        finally:
            self._restore_bootstrap_manifest()

    def _restore_bootstrap_manifest(self):
        """Point manifest.file back at the import-free bootstrap west.yml.

        Keeps the 'sdk' extension command discoverable for subsequent
        invocations regardless of whether upstream sources are cloned.
        """
        try:
            self._west(["config", "manifest.file", "west.yml"], cwd=self.topdir)
        except SystemExit:
            # log.die raises SystemExit; don't mask the original failure.
            pass

    def _set_manifest(self, manifest_rel):
        """Configure west's manifest.path/manifest.file for this build."""
        self._west(["config", "manifest.path", SDK_REPO], cwd=self.topdir)
        self._west(["config", "manifest.file", manifest_rel], cwd=self.topdir)
        log.inf(f"manifest -> {SDK_REPO}/{manifest_rel}")

    # ------------------------------------------------------------------ #
    # PX4 build (native make via shell-out)
    # ------------------------------------------------------------------ #
    def _build_px4(self, cfg, jobs):
        px4_src = os.path.join(self.topdir, "PX4-Autopilot")
        if not os.path.isdir(px4_src):
            log.die(f"PX4 sources not found at {px4_src}. Run without --no-update.")

        env = os.environ.copy()
        venv_bin = self._ensure_px4_venv(px4_src)
        self._ensure_ccache(venv_bin)
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")

        targets = cfg.get("targets") or []
        if not targets:
            log.die("PX4 stack defines no 'targets'.")

        for target in targets:
            log.inf(f"--- px4 make {target} (-j{jobs}) ---")
            self._run(["make", target, f"-j{jobs}"], cwd=px4_src, env=env)

        self._collect_artifacts(os.path.join(px4_src, "build"),
                                cfg.get("artifacts", []) or [])

    def _collect_artifacts(self, src_root, artifacts):
        """Copy built artifacts into bin/realtime/, preserving basenames."""
        import shutil
        out_dir = os.path.join(self.topdir, "bin", "realtime")
        os.makedirs(out_dir, exist_ok=True)
        for rel in artifacts:
            candidate = rel if os.path.isabs(rel) else os.path.join(src_root, rel)
            if not os.path.isfile(candidate):
                log.wrn(f"artifact not found (skipped): {candidate}")
                continue
            dest = os.path.join(out_dir, os.path.basename(candidate))
            shutil.copy2(candidate, dest)
            log.inf(f"collected -> {dest}")

    def _ensure_px4_venv(self, px4_src):
        """Create/refresh a per-workspace venv with PX4's Python requirements.

        Returns the venv's bin directory. Idempotent via a requirements hash
        stamp so we only reinstall when requirements.txt changes.
        """
        import hashlib
        venv = os.path.join(self.topdir, ".px4-venv")
        venv_bin = os.path.join(venv, "bin")
        py = os.path.join(venv_bin, "python")
        if not os.path.isfile(py):
            log.inf(f"Creating PX4 venv at {venv}")
            self._run([sys.executable, "-m", "venv", venv], cwd=self.topdir)
            self._run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel"],
                      cwd=self.topdir)

        req = os.path.join(px4_src, "Tools", "setup", "requirements.txt")
        if os.path.isfile(req):
            with open(req, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            stamp = os.path.join(venv, ".requirements.sha256")
            prev = None
            if os.path.isfile(stamp):
                with open(stamp) as f:
                    prev = f.read().strip()
            if prev != digest:
                log.inf("Installing PX4 Python requirements into venv")
                self._run([py, "-m", "pip", "install", "-r", req], cwd=self.topdir)
                with open(stamp, "w") as f:
                    f.write(digest)
            else:
                log.inf("PX4 venv requirements up to date")
        else:
            log.wrn(f"PX4 requirements.txt not found at {req}")
        return venv_bin

    def _ensure_ccache(self, venv_bin):
        """Provide a ccache passthrough shim if real ccache is absent.

        PX4's nested cyclonedds/idlc invocation ignores USE_CCACHE, so we drop
        a shim named 'ccache' on PATH that just execs its arguments. No-op if a
        real ccache is already available.
        """
        import shutil
        if shutil.which("ccache", path=venv_bin + os.pathsep + os.environ.get("PATH", "")):
            return
        shim = os.path.join(venv_bin, "ccache")
        with open(shim, "w") as f:
            f.write('#!/bin/sh\nexec "$@"\n')
        os.chmod(shim, 0o755)
        log.inf(f"Wrote ccache passthrough shim at {shim}")

    # ------------------------------------------------------------------ #
    # Zephyr / Cerebri build (native west build)
    # ------------------------------------------------------------------ #
    def _build_zephyr(self, cfg, jobs):
        boards = cfg.get("boards") or []
        if not boards:
            log.die("Zephyr/Cerebri stack defines no 'boards'.")
        app = cfg.get("app")

        for board in boards:
            slug = board.replace("/", "_")
            build_dir = os.path.join(self.topdir, "build", slug)
            cmd = ["build", "-b", board, "--build-dir", build_dir]
            if app:
                cmd.append(app)
            log.inf(f"--- west build -b {board} (build-dir {build_dir}) ---")
            self._west(cmd, cwd=self.topdir)

    # ------------------------------------------------------------------ #
    # preflight: fail fast on a clean system with a clear, actionable msg
    # ------------------------------------------------------------------ #
    def _preflight(self, kind):
        """Verify required toolchains/system deps exist before building.

        Installs nothing (many build hosts have no passwordless sudo). It only
        checks and, on failure, prints exactly what is missing and how to
        install it. Use --skip-preflight to bypass.
        """
        log.inf("--- preflight: checking build prerequisites ---")

        if kind == "px4":
            # NuttX/PX4 firmware needs the ARM bare-metal GCC + CMake/Ninja.
            self._check_tools(
                kind="PX4 (NuttX firmware)",
                tools=[
                    ("arm-none-eabi-gcc", "ARM bare-metal GCC toolchain"),
                    ("cmake", "CMake (>= 3.20)"),
                    ("ninja", "Ninja build tool"),
                    ("make", "GNU make"),
                    ("git", "Git"),
                ],
                remedy=(
                    "Install PX4's dependencies with its own setup script:\n"
                    "         bash PX4-Autopilot/Tools/setup/ubuntu.sh\n"
                    "       (or see the Prerequisites section in "
                    "docs/getting_started.rst)"
                ),
            )
        elif kind in ("zephyr", "cerebri"):
            self._check_tools(
                kind="Zephyr / Cerebri",
                tools=[
                    ("cmake", "CMake (>= 3.20)"),
                    ("ninja", "Ninja build tool"),
                    ("dtc", "Device Tree Compiler"),
                    ("git", "Git"),
                ],
                remedy=(
                    "Install the Zephyr SDK and host tools per the Zephyr "
                    "getting-started guide, then export ZEPHYR_SDK_INSTALL_DIR "
                    "(or ZEPHYR_TOOLCHAIN_VARIANT). See the Prerequisites "
                    "section in docs/getting_started.rst."
                ),
            )
            # The Zephyr SDK is not a PATH tool; check it is discoverable.
            self._check_zephyr_sdk()
        else:  # pragma: no cover
            log.wrn(f"No preflight defined for kind '{kind}'; continuing.")
            return

        log.inf("--- preflight: OK ---")

    def _check_tools(self, kind, tools, remedy):
        """Ensure each (executable, description) is on PATH; die if any missing."""
        import shutil
        missing = [(exe, desc) for exe, desc in tools if not shutil.which(exe)]
        if missing:
            lines = [f"Missing {kind} build prerequisites:"]
            for exe, desc in missing:
                lines.append(f"  - {exe:<20} ({desc})")
            lines.append("")
            lines.append(f"Remedy: {remedy}")
            lines.append("")
            lines.append("Re-run with --skip-preflight to bypass this check "
                         "(build may still fail).")
            log.die("\n".join(lines))

    def _check_zephyr_sdk(self):
        """Warn (do not die) if no Zephyr SDK location is discoverable.

        west build can still auto-discover an installed SDK, so a missing env
        var is not necessarily fatal -- surface it as guidance, not an error.
        """
        if os.environ.get("ZEPHYR_SDK_INSTALL_DIR") or \
                os.environ.get("ZEPHYR_TOOLCHAIN_VARIANT"):
            return
        # Common install locations. The SDK typically unpacks as a versioned
        # dir (e.g. ~/zephyr-sdk-0.16.5), so glob for those too.
        import glob as _glob
        candidates = []
        for base in (os.path.expanduser("~"),
                     os.path.expanduser("~/.local/opt"),
                     "/opt"):
            candidates += _glob.glob(os.path.join(base, "zephyr-sdk*"))
        if any(os.path.isdir(c) for c in candidates):
            return
        log.wrn(
            "No Zephyr SDK detected (ZEPHYR_SDK_INSTALL_DIR / "
            "ZEPHYR_TOOLCHAIN_VARIANT unset and no ~/zephyr-sdk found). "
            "west build will try to auto-discover one; if the build fails, "
            "install the Zephyr SDK (see docs/getting_started.rst)."
        )

    # ------------------------------------------------------------------ #
    # low-level helpers
    # ------------------------------------------------------------------ #
    def _west(self, argv, cwd=None, env=None):
        """Invoke west portably: bare 'west' if on PATH, else python -m west."""
        import shutil
        if shutil.which("west"):
            cmd = ["west", *argv]
        else:
            cmd = [sys.executable, "-m", "west", *argv]
        self._run(cmd, cwd=cwd, env=env)

    def _run(self, cmd, cwd=None, env=None):
        log.dbg(f"run: {' '.join(cmd)} (cwd={cwd})")
        try:
            subprocess.run(cmd, cwd=cwd, env=env, check=True)
        except subprocess.CalledProcessError as e:
            log.die(f"Command failed ({e.returncode}): {' '.join(cmd)}")
