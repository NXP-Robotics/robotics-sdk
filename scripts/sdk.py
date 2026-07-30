"""NXP Robotics SDK west extension commands: ``download`` and ``build``.

Uniform, stack-agnostic interface:

    west sdk download --platform <platform> | all
    west sdk update   --platform <platform> [--stack <stack>]
    west sdk build    --platform <platform> [--stack <stack>] [--update]

``build`` fetches sources automatically only when they are missing, so local
edits survive rebuilds; pass ``--update`` to force a refetch (which resets
sources to the manifest revision and discards local changes).

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

        up = sub.add_parser(
            "update",
            help="Set the platform's manifest and clone/update its sources "
                 "(west update) without building",
        )
        up.add_argument("-p", "--platform", required=True,
                        help="Platform whose sources to fetch/update.")
        up.add_argument("-s", "--stack", default=None,
                        help="Stack to update. Defaults to the platform's "
                             "default_stack.")

        bd = sub.add_parser(
            "build",
            help="Init manifest, clone pinned sources (west update), then build",
        )
        bd.add_argument("-p", "--platform", required=True, help="Platform to build.")
        bd.add_argument("-s", "--stack", default=None,
                        help="Stack to build. Defaults to the platform's default_stack.")
        bd.add_argument("-j", "--jobs", default=str(os.cpu_count() or 1),
                        help="Parallel build jobs.")
        bd.add_argument("--update", action="store_true",
                        help="Force a 'west update' before building, even when "
                             "sources are already present. This resets sources "
                             "to the manifest revision and OVERWRITES local "
                             "changes. By default sources are fetched only when "
                             "missing, so local edits are preserved across "
                             "rebuilds.")
        bd.add_argument("--skip-preflight", action="store_true",
                        help="Skip the toolchain/dependency preflight checks.")
        bd.add_argument("--no-venv", dest="use_venv", action="store_false",
                        default=True,
                        help="Do NOT create/use a per-workspace Python venv "
                             "for the PX4 build. Uses the current Python "
                             "environment instead (you are responsible for "
                             "having PX4's Python requirements, e.g. "
                             "kconfiglib, installed). By default a dedicated "
                             ".px4-venv is created and populated.")

        lst = sub.add_parser("list", help="List all supported platforms")

        return parser

    def do_run(self, args, unknown):
        if args.action == "download":
            self._do_download(args)
        elif args.action == "update":
            self._do_update(args)
        elif args.action == "build":
            self._do_build(args)
        elif args.action == "list":
            self._do_list(args)
        else:  # pragma: no cover
            log.die(f"Unknown action: {args.action}")

    def _do_list(self, args):
        platforms = all_platforms(self.topdir)
        if not platforms:
            log.die("No platforms discovered under platforms/*/build.yml.")

        for plat in platforms:
            log.inf(plat)

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
    # update: fetch/update a platform's sources only (no build)
    # ------------------------------------------------------------------ #
    def _do_update(self, args):
        """Set the platform's manifest, run 'west update', then restore the
        import-free bootstrap manifest.

        This is the "just bring in the sources" operation: it clones (or
        updates to the manifest-pinned revision) the upstream projects for the
        selected (stack, platform) without building anything. As with build,
        the bootstrap manifest is always restored afterwards so the 'sdk'
        extension command remains discoverable even if sources are absent.
        """
        pdata, _ppath = load_platform(self.topdir, args.platform)
        stack = resolve_stack(pdata, args.platform, args.stack)

        log.inf(f"=== update: platform={args.platform} stack={stack} ===")

        try:
            self._fetch_sources(stack, args.platform, do_update=True)
        finally:
            self._restore_bootstrap_manifest()
        log.inf(f"--- update: sources ready under {self.topdir} ---")

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

        # Point the workspace manifest at the (stack, platform) file and, when
        # needed, clone/update its sources -- the SAME fetch performed by 'sdk
        # update'. Smart-fetch policy: by default we run 'west update' ONLY
        # when the manifest's project dirs are missing, so repeat builds never
        # clobber local edits. '--update' forces a refetch (resets sources to
        # the manifest revision, discarding local changes).
        #
        # The platform manifests use ``import: true``; if left active after the
        # build, west can no longer discover this 'sdk' extension command
        # whenever the imported sources are absent. So we always restore
        # manifest.file to the import-free bootstrap west.yml afterwards,
        # leaving the workspace in a usable state.
        present = self._sources_present(stack, args.platform)
        do_update = args.update or not present
        if args.update and present:
            log.inf("--update: refetching sources "
                    "(local changes will be reset to the manifest revision).")
        elif not present:
            log.inf("Sources not found; fetching automatically "
                    "(use --update later to refresh).")
        else:
            log.inf("Sources present; skipping fetch "
                    "(use --update to refresh to the manifest revision).")

        try:
            self._fetch_sources(stack, args.platform, do_update=do_update)

            if kind == "px4":
                self._build_px4(cfg, args.jobs, use_venv=args.use_venv)
            elif kind in ("zephyr", "cerebri"):
                self._build_zephyr(cfg, args.jobs)
            elif kind == "am32":
                self._build_am32(cfg, args.jobs)
            else:
                log.die(f"Unsupported build kind '{kind}'.")
        finally:
            self._restore_bootstrap_manifest()

    def _fetch_sources(self, stack, platform, do_update=True):
        """Set the platform manifest and (optionally) run 'west update'.

        Shared by both 'sdk update' and 'sdk build' so the manifest-selection
        and source-fetch logic has a single implementation. Does NOT restore
        the bootstrap manifest -- the caller owns that (build must keep the
        manifest active through compilation), so restore happens in the
        caller's ``finally``.
        """
        manifest_rel = manifest_for(stack, platform)
        self._set_manifest(manifest_rel)
        if do_update:
            self._west(["update"], cwd=self.topdir)
        else:
            log.inf("Skipping 'west update' (sources already present).")

    def _manifest_project_paths(self, stack, platform):
        """Return the workspace-relative project dirs declared in the
        (stack, platform) manifest -- i.e. where west clones each project.

        Excludes 'self' (the SDK repo itself). west defaults a project's path
        to its 'name' when 'path' is omitted, so we mirror that. This reads the
        manifest (the authoritative "what gets cloned and where"), keeping
        sdk.py free of any per-platform source-layout knowledge.
        """
        manifest_rel = manifest_for(stack, platform)
        manifest_abs = os.path.join(_sdk_root(self.topdir), manifest_rel)
        with open(manifest_abs) as f:
            data = (yaml.safe_load(f) or {}).get("manifest", {}) or {}
        paths = []
        for proj in data.get("projects", []) or []:
            p = proj.get("path") or proj.get("name")
            if p:
                paths.append(p)
        return paths

    def _sources_present(self, stack, platform):
        """True only if every manifest-declared project dir exists and is
        non-empty in the workspace.

        Used by the smart-fetch policy: build fetches automatically only when
        this is False. On any error reading the manifest we conservatively
        return False so the caller fetches (never silently skip a needed
        update -- worst case degrades to always-fetch).
        """
        try:
            paths = self._manifest_project_paths(stack, platform)
        except Exception as exc:  # noqa: BLE001 - defensive: force a fetch
            log.wrn(f"Could not determine source presence ({exc}); "
                    f"will fetch to be safe.")
            return False
        if not paths:  # nothing declared -> nothing to fetch
            return True
        for rel in paths:
            d = os.path.join(self.topdir, rel)
            # An empty dir means an interrupted/partial clone -> treat as
            # missing so we (re)fetch and self-heal.
            if not os.path.isdir(d) or not os.listdir(d):
                return False
        return True

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
    def _build_px4(self, cfg, jobs, use_venv=True):
        px4_src = os.path.join(self.topdir, "PX4-Autopilot")
        if not os.path.isdir(px4_src):
            log.die(f"PX4 sources not found at {px4_src}. "
                    f"Run 'west sdk update' (or 'west sdk build --update') first.")

        env = os.environ.copy()
        if use_venv:
            venv_bin = self._ensure_px4_venv(px4_src)
            self._ensure_ccache(venv_bin)
            env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
            # PX4's CMake resolves Python via find_package/PATH and runs
            # "${PYTHON_EXECUTABLE} -c 'import menuconfig'" (kconfiglib). Point it
            # explicitly at the venv interpreter (which has PX4's requirements
            # incl. kconfiglib) so a system python without kconfiglib is not used.
            venv_py = os.path.join(venv_bin, "python")
            if os.path.isfile(venv_py):
                env["PYTHON_EXECUTABLE"] = venv_py
                env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
                env.pop("PYTHONHOME", None)
        else:
            # --no-venv: build against the current Python environment as-is.
            # The caller is responsible for having PX4's Python requirements
            # (e.g. kconfiglib, which provides the 'menuconfig' module PX4's
            # kconfig.cmake needs) installed. Point PX4's CMake at the current
            # interpreter so it does not fall back to an unrelated python.
            log.wrn("--no-venv: skipping PX4 venv; using the current Python "
                    "environment. Ensure PX4's Python requirements "
                    "(e.g. kconfiglib) are installed.")
            venv_bin = None
            env["PYTHON_EXECUTABLE"] = sys.executable

        targets = cfg.get("targets") or []
        if not targets:
            log.die("PX4 stack defines no 'targets'.")

        for target in targets:
            log.inf(f"--- px4 make {target} (-j{jobs}) ---")
            self._run(["make", target, f"-j{jobs}"], cwd=px4_src, env=env)

        self._collect_artifacts(os.path.join(px4_src, "build"),
                                cfg.get("artifacts", []) or [])

        # Optional per-platform post-build signing (navq95 uses MCUboot).
        # No-op unless the platform's build.yml defines a 'sign' block, so
        # sdk.py stays generic.
        self._sign_px4(px4_src, cfg.get("sign"), env, venv_bin)

    def _sign_px4(self, px4_src, sign, env, venv_bin):
        """Sign collected .bin images with MCUboot imgtool (config-driven).

        The 'sign' block comes from the platform's build.yml (present only for
        navq95). imgtool.py lives in the PX4 tree (scripts/imgtool.py) and does
        ``from imgtool import main``, so it must run with PX4-Autopilot/scripts
        importable. Inputs/outputs are basenames inside bin/realtime/.
        """
        if not sign:
            return

        out_dir = os.path.join(self.topdir, "bin", "realtime")
        scripts_dir = os.path.join(px4_src, "scripts")
        tool_rel = sign.get("tool", "scripts/imgtool.py")
        tool = tool_rel if os.path.isabs(tool_rel) else os.path.join(px4_src, tool_rel)
        if not os.path.isfile(tool):
            log.die(f"imgtool not found for signing: {tool}")

        images = sign.get("images") or []
        if not images:
            log.wrn("sign: no images listed; nothing to sign.")
            return

        # imgtool needs a few third-party deps (click, cryptography, intelhex,
        # pyyaml) that are NOT in PX4's requirements.txt. Install them into the
        # workspace venv (idempotent) before signing.
        py = os.path.join(venv_bin, "python") if venv_bin else None
        py = py if (py and os.path.isfile(py)) else sys.executable
        self._ensure_imgtool_deps(py)

        # imgtool imports its sibling package; make it importable.
        sign_env = dict(env)
        sign_env["PYTHONPATH"] = (
            scripts_dir + os.pathsep + sign_env.get("PYTHONPATH", "")
        ).rstrip(os.pathsep)

        log.inf("--- post-build: MCUboot signing (imgtool) ---")
        for img in images:
            in_name = img.get("in")
            out_name = img.get("out")
            if not in_name or not out_name:
                log.wrn(f"sign: skipping malformed image entry: {img}")
                continue
            in_path = os.path.join(out_dir, in_name)
            out_path = os.path.join(out_dir, out_name)
            if not os.path.isfile(in_path):
                log.wrn(f"sign: input not found (skipped): {in_path}")
                continue

            cmd = [py, tool, "sign",
                   "--version", str(sign.get("version", "1.0.0")),
                   "--header-size", str(sign.get("header_size", "0x800")),
                   "--slot-size", str(sign.get("slot_size", "0x700000"))]
            if sign.get("pad_header", True):
                cmd.append("--pad-header")
            # Optional signing key (unsigned/hash-only if omitted).
            key = sign.get("key")
            if key:
                key = key if os.path.isabs(key) else os.path.join(px4_src, key)
                cmd += ["--key", key]
            cmd += [in_path, out_path]

            log.inf(f"sign: {in_name} -> {out_name}")
            self._run(cmd, cwd=scripts_dir, env=sign_env)
            log.inf(f"signed -> {out_path}")

    # imgtool's third-party deps (absent from PX4's requirements.txt).
    _IMGTOOL_DEPS = ["click", "cryptography", "intelhex", "pyyaml", "cbor2"]

    def _ensure_imgtool_deps(self, py):
        """Install MCUboot imgtool's Python deps into the venv (idempotent)."""
        venv = os.path.join(self.topdir, ".px4-venv")
        stamp = os.path.join(venv, ".imgtool-deps.ok")
        if os.path.isfile(stamp):
            log.inf("imgtool deps already installed")
            return
        log.inf("Installing imgtool dependencies into venv "
                f"({', '.join(self._IMGTOOL_DEPS)})")
        self._run([py, "-m", "pip", "install", *self._IMGTOOL_DEPS],
                  cwd=self.topdir)
        try:
            with open(stamp, "w") as f:
                f.write("ok\n")
        except OSError:
            pass

    def _collect_artifacts(self, src_root, artifacts):
        """Copy built artifacts into bin/realtime/, preserving basenames.

        Each entry may be an exact path or a glob pattern (e.g. containing
        '*'); globs are expanded relative to src_root and every match is
        collected.
        """
        import shutil
        import glob as _glob
        out_dir = os.path.join(self.topdir, "bin", "realtime")
        os.makedirs(out_dir, exist_ok=True)
        for rel in artifacts:
            pattern = rel if os.path.isabs(rel) else os.path.join(src_root, rel)
            matches = _glob.glob(pattern)
            if not matches:
                log.wrn(f"artifact not found (skipped): {pattern}")
                continue
            for candidate in sorted(matches):
                if not os.path.isfile(candidate):
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
            # Reinstall if requirements changed OR a sentinel package is
            # missing. The sentinel guards against a stamp that was written
            # after an interrupted/partial install (kconfiglib provides the
            # 'menuconfig' module PX4's kconfig.cmake needs).
            need_install = (prev != digest) or not self._venv_has_sentinel(py)
            if need_install:
                log.inf("Installing PX4 Python requirements into venv")
                self._run([py, "-m", "pip", "install", "-r", req], cwd=self.topdir)
                # Only stamp AFTER verifying the sentinel imports, so a failed
                # install never gets cached as 'up to date'.
                if self._venv_has_sentinel(py):
                    with open(stamp, "w") as f:
                        f.write(digest)
                else:
                    log.wrn("PX4 requirements installed but 'menuconfig' "
                            "(kconfiglib) still not importable; not stamping.")
            else:
                log.inf("PX4 venv requirements up to date")
        else:
            log.wrn(f"PX4 requirements.txt not found at {req}")
        return venv_bin

    @staticmethod
    def _venv_has_sentinel(py):
        """True if the venv python can import kconfiglib's 'menuconfig'."""
        try:
            subprocess.run([py, "-c", "import menuconfig"],
                           check=True,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return True
        except (subprocess.CalledProcessError, OSError):
            return False

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
    # AM32 build (GNU make via shell-out; xPack toolchain via arm_sdk_install)
    # ------------------------------------------------------------------ #
    def _build_am32(self, cfg, jobs):
        am32_src = os.path.join(self.topdir, "AM32")
        if not os.path.isdir(am32_src):
            log.die(f"AM32 sources not found at {am32_src}. "
                    f"Run 'west sdk update' (or 'west sdk build --update') first.")

        targets = cfg.get("targets") or []
        if not targets:
            log.die("AM32 stack defines no 'targets'.")

        # One-time prebuild: AM32 downloads its own xPack arm-none-eabi-gcc
        # toolchain into the tree via 'make arm_sdk_install'. Idempotent -- the
        # target no-ops once the SDK is present, but we also stamp it to avoid
        # re-running the network step on every build.
        stamp = os.path.join(am32_src, ".arm_sdk_installed")
        if not os.path.isfile(stamp):
            log.inf("--- am32 prebuild: make arm_sdk_install (download toolchain) ---")
            self._run(["make", "arm_sdk_install"], cwd=am32_src)
            try:
                open(stamp, "w").write("ok\n")
            except OSError:
                pass
        else:
            log.inf("am32 toolchain already installed (arm_sdk_install)")

        for target in targets:
            log.inf(f"--- am32 make {target} (-j{jobs}) ---")
            self._run(["make", target, f"-j{jobs}"], cwd=am32_src)

        # AM32 emits versioned files into obj/; collect as-is (globs supported).
        self._collect_artifacts(am32_src, cfg.get("artifacts", []) or [])

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
        elif kind == "am32":
            # AM32 downloads its own xPack arm-none-eabi-gcc via
            # 'make arm_sdk_install', so no host ARM GCC is required -- but the
            # download + build needs make/git/wget/tar and python3.
            self._check_tools(
                kind="AM32 (MCXA firmware)",
                tools=[
                    ("make", "GNU make"),
                    ("git", "Git"),
                    ("wget", "wget (fetches the xPack toolchain)"),
                    ("tar", "tar (unpacks the xPack toolchain)"),
                    ("python3", "Python 3 (binary post-processing)"),
                ],
                remedy=(
                    "Install the listed host tools (e.g. on Ubuntu: "
                    "sudo apt install make git wget tar python3). The ARM "
                    "toolchain itself is downloaded automatically by "
                    "'make arm_sdk_install'. See the Prerequisites section "
                    "in docs/getting_started.rst."
                ),
            )
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
