"""NXP Robotics SDK west extension commands: ``download`` and ``build``.

Uniform, stack-agnostic interface:

    west sdk download --platform <platform> | all
    west sdk update   --platform <platform> [--stack <stack>]
    west sdk build    --platform <platform> [--stack <stack>] [--update]

``build`` fetches sources automatically only when they are missing, so local
edits survive rebuilds; pass ``--update`` to force a refetch (which resets
sources to the manifest revision).

Switching platforms is DESTRUCTIVE: all PX4 platforms share the same
``PX4-Autopilot/`` workspace dir, so if the currently-checked-out sources
belong to a different platform (different remote or pinned revision), they are
purged and re-cloned for the requested platform. This prevents building one
platform against another's tree (e.g. mr-vmu-rt1176 on the NavQ95 fork, where the
make target does not exist).

Both require an initialized west workspace (run ``west init -l robotics-sdk``
once) because west only discovers extension commands after init.

sdk.py contains NO platform-specific knowledge. Each platform owns a single
definition file ``platforms/<platform>/build.yml`` describing its stacks
(kind, targets/boards, app, artifacts) and its ``download`` catalog.

``download`` fetches prebuilt artifacts directly over HTTPS from the base
URL described by the authoritative root ``versions.yml`` (one base URL per
artifact class). The SDK already knows which files each platform needs from
its ``download:`` catalog, so there is no listing, index or REST call: each
file is fetched from ``<class base_url>/<subpath>/<filename>``. Hosts that
require credentials are handled transparently via ``~/.netrc`` (no prompt).
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
SDK_REPO = "robotics-sdk"

# Authoritative artifact base URLs live in this root file (versions.yml).
VERSIONS_FILE = "versions.yml"


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


def load_versions(topdir):
    """Load the authoritative artifact source/version file (root versions.yml).

    Returns the 'artifacts' mapping: {<class>: {base_url}, ...}. Each class
    carries a single base URL; a platform may override a whole class under
    its own 'artifacts' block. sdk.py stays platform-agnostic.
    """
    if yaml is None:
        log.die("PyYAML is required. Install with: pip install pyyaml")
    path = os.path.join(_sdk_root(topdir), VERSIONS_FILE)
    if not os.path.isfile(path):
        log.die(f"Missing {VERSIONS_FILE} at {path}.")
    with open(path) as f:
        data = (yaml.safe_load(f) or {}).get("artifacts", {}) or {}
    if not data:
        log.die(f"{VERSIONS_FILE} has no 'artifacts' block.")
    return data


# --------------------------------------------------------------------------- #
# Artifact fetcher for `west sdk download`.
#
# Fetches a known URL to a local file over HTTPS -- no listing, index or
# REST call. Swapping the publish location (e.g. an internal Nexus vs the
# public site) is a matter of changing the base URLs in versions.yml.
# Self-contained (stdlib + requests) so it works in a plain customer
# checkout. Credentials, where a host needs them, come from ~/.netrc
# automatically (no prompt); the token is never logged or echoed.
# --------------------------------------------------------------------------- #
class _StoreError(Exception):
    """Any artifact transfer failure."""


class _ArtifactStore:
    """Minimal artifact fetcher: HTTPS GET a known URL to a local file.

    There is NO listing, index or REST call: the SDK already knows which
    files to fetch per platform (platforms/*/build.yml `download:` catalogs),
    so each artifact is fetched directly from
        <class base_url>/<subpath>/<filename>
    The internal Nexus and the public site are both plain static hosts; the
    only thing that changes between them is the base URL in versions.yml.
    ``requests`` reads ~/.netrc automatically, so hosts that require a token
    keep working with no interactive prompt.
    """

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._session = None

    def _requests(self):
        try:
            import requests  # noqa: F401
            return requests
        except ImportError:
            log.die("The 'requests' package is required for 'west sdk "
                    "download'. Install it with: pip install requests")

    @property
    def session(self):
        if self._session is None:
            requests = self._requests()
            self._session = requests.Session()
            # Internal Nexus uses an internal CA; SSL verification is disabled
            # to match the rest of the SDK's internal tooling.
            self._session.verify = False
            try:
                import urllib3
                urllib3.disable_warnings()
            except Exception:  # pragma: no cover - best effort
                pass
        return self._session

    # -- transfer ------------------------------------------------------- #
    def download(self, url, out_path):
        """Stream a URL to out_path (atomic via a .part temp file)."""
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp = out_path + ".part"
        try:
            with self.session.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    raise _StoreError(f"not found (HTTP 404): {url}")
                if r.status_code == 401:
                    raise _StoreError(
                        f"authentication failed (HTTP 401): {url}. "
                        f"Check your ~/.netrc token for that host.")
                if r.status_code == 403:
                    raise _StoreError(
                        f"access forbidden (HTTP 403): {url}. "
                        f"Your account may lack read permission.")
                if r.status_code != 200:
                    raise _StoreError(f"HTTP {r.status_code} fetching {url}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except _StoreError:
            self._rm(tmp)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rm(tmp)
            raise _StoreError(f"transfer failed: {exc}")
        os.replace(tmp, out_path)

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass


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
            help="Download prebuilt artifacts (firmware, and Linux WIC for mr-navq95) from the artifact store",
        )
        dl.add_argument("-p", "--platform", required=True,
                        help="Platform to download for, or 'all'.")
        dl.add_argument("--dry-run", action="store_true",
                        help="Resolve and print what would be fetched without "
                             "downloading anything.")

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
        bd.add_argument("--shield", default=None,
                        help="Zephyr stack only: select the shield example set "
                             "to build (mr-mcxn-t1: t1|oflo|f9p). Defaults to "
                             "the platform's 'default_shield'. Not used by the "
                             "cerebri stack, which maps each app to its shield.")
        bd.add_argument("--build-all", dest="build_all", action="store_true",
                        help="Build ALL non-caveat examples for the selected "
                             "shield/stack, including any marked "
                             "default: false. With no --shield/--stack it "
                             "spans every stack. Cannot be combined with a "
                             "named <example>.")
        bd.add_argument("example", nargs="?", default=None,
                        help="Optional single example/app (by name) to build. "
                             "zephyr: one example within the selected shield; "
                             "cerebri: one app. Omitted -> all non-caveat "
                             "examples (zephyr) or all apps (cerebri).")
        bd.add_argument("-j", "--jobs", default=str(os.cpu_count() or 1),
                        help="Parallel build jobs.")
        bd.add_argument("--update", action="store_true",
                        help="Force a 'west update' before building, even when "
                             "sources are already present. This resets sources "
                             "to the manifest revision. By default sources are "
                             "fetched only when missing, so local edits are "
                             "preserved across rebuilds.")
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
        lst.add_argument("-v", "--verbose", "--examples", dest="verbose",
                         action="store_true",
                         help="Also show each platform's stacks, shields "
                              "and examples. Without this flag, 'list' prints "
                              "ONE platform name per line (stable, "
                              "machine-parseable contract for CI).")

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
            # Default output is ONE platform name per line -- a stable
            # contract for CI. The stack/shield/example matrix is opt-in
            # via -v/--verbose so it never pollutes the parseable list.
            if not getattr(args, "verbose", False):
                continue
            try:
                pdata, _ = load_platform(self.topdir, plat)
            except SystemExit:
                continue
            for sname, scfg in (pdata.get("stacks") or {}).items():
                scfg = scfg or {}
                shields = scfg.get("shields")
                examples = scfg.get("examples")
                apps = scfg.get("app")
                if shields:
                    default_shield = pdata.get("default_shield")
                    for shield, sh in shields.items():
                        tag = " (default)" if shield == default_shield else ""
                        log.inf(f"  [{sname}] --shield {shield}{tag}")
                        for ex in (sh or {}).get("examples", []):
                            if ex.get("expect_fail"):
                                m = "  [expect_fail]"
                            elif not ex.get("default", True):
                                m = "  [not default]"
                            else:
                                m = ""
                            log.inf(f"      {ex.get('name')}{m}")
                if examples:
                    log.inf(f"  [{sname}]")
                    for ex in examples:
                        log.inf(f"      {ex.get('name')}")
                if apps:
                        log.inf(f"  [{sname}]\n      {apps}")
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

        # Generic (default) artifact base URLs. A platform may
        # override a whole class under its own 'artifacts:' block.
        art = load_versions(self.topdir)

        # One shared fetcher client (a single requests session, reused).
        store = _ArtifactStore(dry_run=getattr(args, "dry_run", False))

        overall_ok = True
        for platform in targets:
            pdata, _ = load_platform(self.topdir, platform)
            entries = pdata.get("download", []) or []
            if not entries:
                log.wrn(f"[{platform}] no download entries defined; skipping.")
                continue

            log.inf(f"=== download: {platform} ===")
            for entry in entries:
                ok = self._download_one(store, platform, entry, pdata, art)
                overall_ok = overall_ok and ok

        if not overall_ok:
            log.die("One or more artifacts failed to download. See messages above.")

    def _resolve_class(self, pdata, art, cls):
        """Resolve an artifact class: a platform 'artifacts' block overrides the
        generic versions.yml at the whole-class level; else fall back to the
        generic definition. Returns the class dict or None.
        """
        if not cls:
            return None
        pov = pdata.get("artifacts") or {}
        if cls in pov and pov[cls]:
            return pov[cls]
        if cls in art and art[cls]:
            return art[cls]
        return None

    def _realtime_dir(self, pdata, platform):
        """Resolve a platform's firmware output directory: bin/<platform>/<sub>.

        The optional top-level 'realtime_subdir' key in build.yml names the
        firmware sub-directory under bin/<platform>/. It defaults to
        "realtime"; set it to "" to place artifacts directly under
        bin/<platform>/ (used by platforms that have no co-located extra
        images such as an Ubuntu WIC). Keeps sdk.py free of per-platform
        branching -- the layout is data-driven from build.yml.
        """
        subdir = pdata.get("realtime_subdir", "realtime")
        parts = [self.topdir, "bin", platform]
        if subdir:
            parts.append(subdir)
        return os.path.join(*parts)

    def _download_one(self, store, platform, entry, pdata, art):
        """Fetch a single download-catalog entry by direct URL.

        Catalog entry keys (data-driven from build.yml; sdk.py has no
        per-platform knowledge):

          name    : output basename (also the store-side filename unless
                    'src' overrides it).
          class   : artifact class ('mcu'/'mpu'); supplies the base URL,
                    resolved from the platform's 'artifacts' override, else the
                    root versions.yml.
          subpath : directory under the class base URL (e.g. 'mr-navq95').
          src     : store-side filename if it differs from 'name'.
          dest    : sub-path under bin/<platform>/. If it names a file it is
                    kept verbatim; a bare dir gets the filename appended.

        The artifact is fetched directly from
            <class base_url>/<subpath>/<filename>
        No listing, index or REST call is used. Returns True on success (or
        dry-run), False on failure.
        """
        name = entry.get("name", "<unnamed>")
        cls = entry.get("class")
        ccfg = self._resolve_class(pdata, art, cls)
        if not ccfg or not ccfg.get("base_url"):
            log.err(f"  - {name}")
            log.err(f"    ERROR: class {cls!r} needs a 'base_url' (define it in "
                    f"{VERSIONS_FILE} or the platform's 'artifacts' block).")
            return False

        base_url = ccfg["base_url"].rstrip("/")
        subpath = (entry.get("subpath") or "").strip("/")
        # store-side filename: 'src' if given, else 'name'.
        fname = entry.get("src") or name

        url_parts = [base_url]
        if subpath:
            url_parts.append(subpath)
        url_parts.append(fname)
        url = "/".join(url_parts)

        # Local destination. 'dest' is relative to bin/<platform>/. A dest that
        # names a file is kept verbatim; a bare dir gets the filename appended.
        rel = entry.get("dest", "") or ""
        if rel.endswith("/") or rel.endswith(os.sep) or not os.path.splitext(rel)[1]:
            rel = os.path.join(rel, fname)
        out = os.path.join(self.topdir, "bin", platform, rel)

        log.inf(f"  - {name}")
        if store.dry_run:
            log.inf(f"    would fetch : {url}")
            log.inf(f"    would write : {out}")
            return True

        try:
            store.download(url, out)
        except _StoreError as exc:
            log.err(f"    ERROR downloading: {exc}")
            return False
        log.inf(f"    saved -> {out}")
        return True

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

        # A platform switch is destructive: if the workspace currently holds a
        # different platform's sources (different remote/revision), purge them
        # before fetching so 'west update' cannot leave a stale tree behind.
        self._purge_on_mismatch(stack, args.platform)

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

        if getattr(args, "build_all", False) and args.example:
            log.die("--build-all cannot be combined with a named <example>.")

        # Bare invocation (no --stack, no --shield, no <example>): for a
        # platform that defines a curated default set (default: true across
        # its shields/apps, e.g. mr-mcxn-t1), build that default set across
        # ALL stacks. For LEGACY platforms (no shields, no default flags -- a
        # single 'app' per stack, e.g. mr-navq95/tropic/mr-vmu-rt1176/mr-esc-am32-mcxa133),
        # preserve the original behaviour: build the platform's default_stack.
        bare = (args.stack is None and args.shield is None and args.example is None)
        build_all = getattr(args, "build_all", False)
        if bare and (build_all or self._stacks_with_defaults(pdata) or pdata.get("build_all")):
            self._build_all_stacks(pdata, ppath, args)
            return

        stack = resolve_stack(pdata, args.platform, args.stack)
        self._build_one_stack(pdata, ppath, args.platform, stack, args,
                              build_all=build_all)

    def _stacks_with_defaults(self, pdata):
        """Return stack names that have at least one default:true target."""
        out = []
        for sname, scfg in (pdata.get("stacks") or {}).items():
            scfg = scfg or {}
            has = False
            shields = scfg.get("shields")
            if shields:
                for sh in shields.values():
                    if any(e.get("default", True) for e in (sh or {}).get("examples", [])):
                        has = True
            if scfg.get("apps") and any(a.get("default", True) for a in scfg["apps"]):
                has = True
            if has:
                out.append(sname)
        return out

    def _build_all_stacks(self, pdata, ppath, args):
        """Build the default set (or all non-caveat with --build-all) across
        every stack that has such targets. Each stack uses its own manifest,
        so we set+fetch per stack. Zephyr/cerebri kinds only."""
        build_all = getattr(args, "build_all", False)
        if build_all or pdata.get("build_all"):
            stacks = list((pdata.get("stacks") or {}).keys())
        else:
            stacks = self._stacks_with_defaults(pdata)
        if not stacks:
            log.die(f"Platform '{args.platform}' has no buildable default "
                    f"targets. Name a shield/stack/example explicitly.")
        log.inf(f"=== build: platform={args.platform} stacks={', '.join(stacks)} "
                f"({'all non-caveat' if build_all else 'default set'}) ===")
        # Multiple stacks share one west workspace but pin different sources
        # (e.g. mr-mcxn-t1 zephyr vs cerebri resolve hal_afbr to different
        # revisions via their imports). A stack switch must therefore re-run
        # 'west update' so imported modules move to THIS stack's pinned rev;
        # the smart-fetch "sources present -> skip" heuristic only inspects
        # top-level projects and would otherwise leave imported modules stale.
        force_update = len(stacks) > 1
        for stack in stacks:
            self._build_one_stack(pdata, ppath, args.platform, stack, args,
                                  build_all=build_all,
                                  force_update=force_update)

    def _build_one_stack(self, pdata, ppath, platform, stack, args,
                         build_all=False, force_update=False):
        cfg = dict(pdata["stacks"][stack])
        kind = cfg.get("kind")
        if not kind:
            log.die(f"Stack '{stack}' in {ppath} has no 'kind'.")

        log.inf(f"=== build: platform={platform} stack={stack} kind={kind} ===")

        # zephyr-export and blobs-fetch are one-shot guards; reset them per
        # stack so each stack re-runs them (a stack switch may have re-run
        # 'west update' and changed imported module revisions).
        self._zephyr_exported = False
        self._blobs_fetched = False

        if not args.skip_preflight:
            self._preflight(kind)
        else:
            log.wrn("Skipping preflight checks (--skip-preflight).")

        purged = self._purge_on_mismatch(stack, platform)
        present = self._sources_present(stack, platform)
        do_update = args.update or force_update or not present
        if purged:
            log.inf("Sources belonged to a different platform/stack and were "
                    "purged; fetching this stack's pinned sources.")
        elif force_update:
            log.inf("Multi-stack build: refetching so imported modules match "
                    "this stack's manifest revision.")
        elif args.update and present:
            log.inf("--update: refetching sources "
                    "(local changes will be reset to the manifest revision).")
        elif not present:
            log.inf("Sources not found; fetching automatically "
                    "(use --update later to refresh).")
        else:
            log.inf("Sources present; skipping fetch "
                    "(use --update to refresh to the manifest revision).")

        try:
            self._fetch_sources(stack, platform, do_update=do_update)

            if kind == "px4":
                self._build_px4(cfg, args.jobs, platform, pdata,
                                use_venv=args.use_venv)
            elif kind == "zephyr":
                self._zephyr_export()
                self._fetch_blobs(pdata)
                self._build_zephyr(cfg, args.jobs, platform, pdata,
                                   shield=args.shield, example=args.example,
                                   build_all=build_all)
            elif kind == "cerebri":
                self._zephyr_export()
                self._fetch_blobs(pdata)
                self._build_cerebri(cfg, args.jobs, platform, pdata,
                                    app_name=args.example, build_all=build_all)
            elif kind == "am32":
                self._build_am32(cfg, args.jobs, platform, pdata)
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

    def _manifest_projects(self, stack, platform):
        """Return the projects a (stack, platform) manifest declares, resolved
        to what west would actually clone.

        Each item is a dict: {'path', 'name', 'url', 'revision'}. 'url' is the
        fully-resolved fetch URL (remote url-base + repo-path/name), matching
        what 'git remote get-url origin' reports after a clone. Excludes
        'self'. Keeps sdk.py free of per-platform knowledge -- everything is
        read from the manifest.
        """
        manifest_rel = manifest_for(stack, platform)
        manifest_abs = os.path.join(_sdk_root(self.topdir), manifest_rel)
        with open(manifest_abs) as f:
            data = (yaml.safe_load(f) or {}).get("manifest", {}) or {}
        remotes = {r.get("name"): (r.get("url-base") or "").rstrip("/")
                   for r in data.get("remotes", []) or []}
        out = []
        for proj in data.get("projects", []) or []:
            name = proj.get("name")
            if not name:
                continue
            path = proj.get("path") or name
            base = remotes.get(proj.get("remote"), "")
            repo = proj.get("repo-path") or name
            url = f"{base}/{repo}" if base else repo
            out.append({"path": path, "name": name, "url": url,
                        "revision": proj.get("revision")})
        return out

    @staticmethod
    def _git_out(argv, cwd):
        """Run a git command and return stripped stdout, or None on failure."""
        try:
            res = subprocess.run(["git", *argv], cwd=cwd, check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 text=True)
            return res.stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            return None

    @staticmethod
    def _same_remote(a, b):
        """Compare two git remote URLs, ignoring a trailing '.git' and case."""
        def norm(u):
            return (u or "").strip().rstrip("/").removesuffix(".git").lower()
        return norm(a) == norm(b)

    def _sources_match(self, stack, platform):
        """True only if every present project checkout matches the manifest's
        remote URL AND is checked out at the manifest revision.

        Returns False if any declared project is present but points at a
        different remote or a different commit (i.e. it belongs to another
        platform / an older pin). Missing projects are NOT a mismatch here --
        absence is handled by _sources_present (they simply get fetched).
        """
        try:
            projects = self._manifest_projects(stack, platform)
        except Exception as exc:  # noqa: BLE001 - defensive
            log.wrn(f"Could not read manifest projects ({exc}); "
                    f"treating sources as mismatched to force a refetch.")
            return False
        for proj in projects:
            d = os.path.join(self.topdir, proj["path"])
            if not os.path.isdir(d) or not os.listdir(d):
                continue  # absent -> not a 'mismatch', just needs fetching
            # Remote check. west names the remote after the manifest's
            # 'remote:' entry (NOT necessarily 'origin'), so compare the
            # manifest URL against ALL configured remotes; a match on any one
            # means the right upstream is wired up.
            remotes_out = self._git_out(["remote", "-v"], cwd=d)
            if remotes_out:
                urls = set()
                for line in remotes_out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        urls.add(parts[1])
                if urls and not any(self._same_remote(u, proj["url"]) for u in urls):
                    log.inf(f"  remote mismatch in {proj['path']}: "
                            f"have {sorted(urls)}, want {proj['url']!r}")
                    return False
            # Revision check: compare HEAD against the revision THIS
            # manifest pins (proj['revision']), NOT against 'manifest-rev'.
            #
            # 'manifest-rev' records the revision of the LAST 'west update',
            # which -- for a platform/stack switch that reuses the same
            # workspace path (e.g. mr-navq95 and tropic both clone PX4 into
            # 'PX4-Autopilot' but pin different commits) -- still points at the
            # PREVIOUS platform's commit. Trusting manifest-rev therefore made
            # a stale tree look like a match ('head == manifest-rev') and
            # skipped the needed re-fetch. So we resolve the manifest's pinned
            # revision directly and treat HEAD != pinned as a real mismatch.
            #
            # A pinned SHA/tag resolves locally whenever the tree exists; a
            # pinned BRANCH name may not resolve until fetched -- in that case
            # we cannot prove a mismatch by revision alone, so we fall back to
            # manifest-rev (best-effort) and, if that too is inconclusive,
            # assume a match (avoids a spurious destructive re-fetch on a mere
            # branch-vs-SHA form difference; a genuine platform switch is still
            # caught by the remote/URL check above).
            want = proj.get("revision")
            if want:
                head = self._git_out(["rev-parse", "HEAD"], cwd=d)
                # Authoritative target: the revision this manifest pins.
                target = self._git_out(
                    ["rev-parse", f"{want}^{{commit}}"], cwd=d)
                if target is None:
                    # Pinned revision (likely an unfetched branch name) does
                    # not resolve locally -> fall back to manifest-rev.
                    target = self._git_out(
                        ["rev-parse", "manifest-rev^{commit}"], cwd=d)
                if head is not None and target is not None and head != target:
                    log.inf(f"  revision mismatch in {proj['path']}: "
                            f"HEAD={head}, want {want} (resolved={target}).")
                    return False
        return True

    def _purge_sources(self, stack, platform):
        """Delete the workspace project dirs declared by the (stack, platform)
        manifest. Used to make a platform switch destructive.

        Only removes manifest-declared project paths (never the SDK repo, and
        never bin/ / build/ / .px4-venv). Logs each removal.
        """
        import shutil
        try:
            projects = self._manifest_projects(stack, platform)
        except Exception as exc:  # noqa: BLE001
            log.wrn(f"Could not read manifest projects for purge ({exc}).")
            return
        for proj in projects:
            path = proj["path"]
            if not path or path in (".", SDK_REPO) or os.path.isabs(path):
                continue  # never touch the SDK repo or escape the workspace
            d = os.path.join(self.topdir, path)
            if os.path.isdir(d):
                log.inf(f"purging sources: {d}")
                shutil.rmtree(d, ignore_errors=True)

    def _purge_on_mismatch(self, stack, platform):
        """Destructive platform-switch guard.

        If sources are present but do NOT match this platform's manifest
        (different remote or revision), purge them and return True. Otherwise
        return False (nothing purged).
        """
        if not self._sources_present(stack, platform):
            return False  # nothing there -> normal fetch handles it
        if self._sources_match(stack, platform):
            return False  # already the right sources -> keep local edits
        log.wrn("Detected sources for a different platform/revision; "
                "performing a destructive re-fetch (local changes to the "
                "existing tree will be discarded).")
        self._purge_sources(stack, platform)
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
    def _build_px4(self, cfg, jobs, platform, pdata, use_venv=True):
        px4_src = os.path.join(self.topdir, "PX4-Autopilot")
        if not os.path.isdir(px4_src):
            log.die(f"PX4 sources not found at {px4_src}. "
                    f"Run 'west sdk update' (or 'west sdk build --update') first.")

        targets = cfg.get("targets") or []
        if not targets:
            log.die("PX4 stack defines no 'targets'.")

        for target in targets:
            log.inf(f"--- px4 make {target} (-j{jobs}) ---")
            self._run(["make", target, f"-j{jobs}"], cwd=px4_src)

        out_dir = self._realtime_dir(pdata, platform)
        self._collect_artifacts(os.path.join(px4_src, "build"),
                                cfg.get("artifacts", []) or [], out_dir)

        # Optional per-platform post-build signing (mr-navq95 uses MCUboot).
        # No-op unless the platform's build.yml defines a 'sign' block, so
        # sdk.py stays generic.
        self._sign_px4(px4_src, cfg.get("sign"), out_dir)
        self._package_px4(px4_src, cfg.get("package"), out_dir)

    def _package_px4(self, px4_src, package, out_dir):
        """Re-run PX4's px_mkfw.py against a post-signing image (config-driven).

        'package' comes from the platform's build.yml (present only where a
        post-signing re-wrap is needed, e.g. mr-navq95 -- PX4's build already
        auto-packages the unsigned .bin, so this repeats that same step
        against the *signed* .bin instead). px_mkfw.py needs parameters.xml
        / airframes.xml from the target's own build dir (generated by the
        same 'make <target>' that produced the unsigned image; unaffected by
        signing) plus the board's firmware.prototype. It writes JSON to
        stdout, which we redirect to the .px4 output ourselves.
        """
        if not package:
            return
        tool_rel = package.get("tool", "Tools/px_mkfw.py")
        tool = tool_rel if os.path.isabs(tool_rel) else os.path.join(px4_src, tool_rel)
        if not os.path.isfile(tool):
            log.die(f"px_mkfw.py not found for packaging: {tool}")
        proto_rel = package.get("prototype")
        if not proto_rel:
            log.die("package: 'prototype' is required.")
        proto = proto_rel if os.path.isabs(proto_rel) else os.path.join(px4_src, proto_rel)
        if not os.path.isfile(proto):
            log.die(f"package: prototype not found: {proto}")
        build_target = package.get("build_target")
        if not build_target:
            log.die("package: 'build_target' is required "
                    "(locates parameters.xml/airframes.xml).")
        build_dir = os.path.join(px4_src, "build", build_target)
        param_xml = os.path.join(build_dir, "parameters.xml")
        airframe_xml = os.path.join(build_dir, "airframes.xml")
        for p in (param_xml, airframe_xml):
            if not os.path.isfile(p):
                log.die(f"package: required PX4 build output not found: {p} "
                        f"(expected from 'make {build_target}')")
        images = package.get("images") or []
        if not images:
            log.wrn("package: no images listed; nothing to package.")
            return
        log.inf("--- post-build: PX4 firmware packaging (px_mkfw.py) ---")
        for img in images:
            in_name, out_name = img.get("in"), img.get("out")
            if not (in_name and out_name):
                log.wrn(f"package: skipping malformed image entry: {img}")
                continue
            in_path = os.path.join(out_dir, in_name)
            out_path = os.path.join(out_dir, out_name)
            if not os.path.isfile(in_path):
                log.wrn(f"package: input not found (skipped): {in_path}")
                continue
            cmd = [tool,
                   "--prototype", proto,
                   "--git_identity", px4_src,
                   "--parameter_xml", param_xml,
                   "--airframe_xml", airframe_xml,
                   "--image", in_path]
            log.inf(f"package: {in_name} -> {out_name}")
            with open(out_path, "w") as f:
                try:
                    subprocess.run(cmd, cwd=px4_src, stdout=f, check=True)
                except subprocess.CalledProcessError as e:
                        log.die(f"Command failed ({e.returncode}): {' '.join(cmd)}")
            log.inf(f"packaged -> {out_path}")

    def _sign_px4(self, px4_src, sign, out_dir):
        """Sign collected .bin images with MCUboot imgtool (config-driven).

        The 'sign' block comes from the platform's build.yml (present only for
        mr-navq95). imgtool.py lives in the PX4 tree (scripts/imgtool.py) and does
        ``from imgtool import main``, so it must run with PX4-Autopilot/scripts
        importable. Inputs/outputs are basenames inside the platform's
        realtime output directory (out_dir).
        """
        if not sign:
            return

        scripts_dir = os.path.join(px4_src, "scripts")
        tool_rel = sign.get("tool", "scripts/imgtool.py")
        tool = tool_rel if os.path.isabs(tool_rel) else os.path.join(px4_src, tool_rel)
        if not os.path.isfile(tool):
            log.die(f"imgtool not found for signing: {tool}")

        images = sign.get("images") or []
        if not images:
            log.wrn("sign: no images listed; nothing to sign.")
            return

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

            cmd = [tool, "sign",
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
            self._run(cmd, cwd=scripts_dir)
            log.inf(f"signed -> {out_path}")

    def _collect_artifacts(self, src_root, artifacts, out_dir):
        """Copy built artifacts into out_dir, preserving basenames.

        out_dir is the platform's resolved realtime output directory (see
        _realtime_dir). Each entry may be an exact path or a glob pattern
        (e.g. containing '*'); globs are expanded relative to src_root and
        every match is collected.
        """
        import shutil
        import glob as _glob
        os.makedirs(out_dir, exist_ok=True)
        for rel in artifacts:
            pattern = rel if os.path.isabs(rel) else os.path.join(src_root, rel)
            matches = _glob.glob(pattern)
            if not matches:
                log.die(f"required artifact not found: {pattern}")
            for candidate in sorted(matches):
                if not os.path.isfile(candidate):
                    continue
                dest = os.path.join(out_dir, os.path.basename(candidate))
                shutil.copy2(candidate, dest)
                log.inf(f"collected -> {dest}")

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
    # Overlay resolution (M2): SDK is authoritative for shield->overlay.
    # Overlays are REFERENCED from pulled repos; the SDK owns no DTS/overlay.
    def _shield_overlay(self, pdata, shield):
        rel = (pdata.get("overlays") or {}).get(shield)
        return os.path.join(self.topdir, rel) if rel else None

    def _zephyr_build_one(self, board, name, app, overlay, group,
                          shield_name=None,
                          sysbuild=False, sysbuild_args=None,
                          platform=None, stack=None, collect_dir=None,
                          image_name=None, extra_modules=None):
        bslug = board.replace("/", "_")
        sub = name or bslug
        if group is not None and platform is not None:
            # Shield/stack platforms: build/<platform>/<stack>/<shield>/<example>.
            # The <stack> segment keeps e.g. cerebri/oflo/optical_flow from
            # colliding with zephyr/oflo/*.
            build_dir = os.path.join(self.topdir, "build", platform,
                                     stack or "", group, sub)
        else:
            # Legacy single-stack/single-app platforms (mr-navq95, tropic,
            # mr-vmu-rt1176): unchanged -> build/<board-slug>.
            build_dir = os.path.join(self.topdir, "build", bslug)
        cmd = ["build", "-b", board, "--build-dir", build_dir]
        if sysbuild:
            # Build MCUboot + the signed app together (two-image sysbuild).
            cmd.insert(1, "--sysbuild")
        if app:
            cmd.append(app)
        # CMake/sysbuild extra args go after a single '--'.
        extra = []
        if overlay:
            extra.append(f"-DEXTRA_DTC_OVERLAY_FILE={overlay}")
        if shield_name:
             # Use the Zephyr shield mechanism (-DSHIELD=) so that both the
             # shield's .overlay and .conf files are automatically applied.
            extra.append(f"-DSHIELD={shield_name}")
        if extra_modules:
            # Register an out-of-tree Zephyr module whose module.yml is NOT at
            # the west project root (e.g. the mcxn-t1-ros2-nodes module nested
            # in the robotics-apps monorepo). Scoped to this build only, so
            # other apps in the same repo are unaffected.
            mods = extra_modules if isinstance(extra_modules, (list, tuple)) \
                else [extra_modules]
            abs_mods = [m if os.path.isabs(m) else os.path.join(self.topdir, m)
                        for m in mods]
            extra.append("-DEXTRA_ZEPHYR_MODULES=" + ";".join(abs_mods))
        if sysbuild and sysbuild_args:
            # Absolutize workspace-relative file paths in -D...FILE= args so
            # CMake resolves them against the workspace, not the app source dir.
            for arg in sysbuild_args:
                if "FILE=" in arg and "=" in arg:
                    key, val = arg.split("=", 1)
                    if val and not os.path.isabs(val):
                        val = os.path.join(self.topdir, val)
                    extra.append(f"{key}={val}")
                else:
                    extra.append(arg)
        if extra:
            cmd += ["--", *extra]
        tag = "sysbuild " if sysbuild else ""
        label = f"{group}/{name}" if group else board
        log.inf(f"--- west {tag}build -b {board} [{label}] "
                f"(build-dir {build_dir}) ---")
        ok = self._west_try(cmd, cwd=self.topdir)
        if ok:
            # Collect the flashable images into bin/, mirroring the build tree:
            #   bin/<platform>/<stack>/<shield>/<example>/{zephyr.signed.bin,
            #                                               mcuboot.bin}
            # (legacy single-app platforms -> bin/<platform>/<board-slug>/).
            if collect_dir:
                # Explicit collect-destination override (build.yml
                # `collect_dir`): land images under bin/<collect_dir>/
                # regardless of the platform/board-slug derivation. Lets an
                # app built on one platform be collected under another
                # platform's bin tree (e.g. build on mr-vmu-rt1176, collect
                # under bin/mr-esc-am32-mcxa133/passthrough/mr-vmu-rt1176/).
                out_dir = os.path.join(self.topdir, "bin", collect_dir)
            elif group is not None and platform is not None:
                out_dir = os.path.join(self.topdir, "bin", platform,
                                       stack or "", group, sub)
            else:
                out_dir = os.path.join(self.topdir, "bin", platform or bslug,
                                       bslug)
            self._collect_zephyr_images(build_dir, sub, out_dir, app,
                                        image_name=image_name)
        return ok

    def _collect_zephyr_images(self, build_dir, name, out_dir, app,
                               image_name=None):
        """Copy the flashable images produced by a (sysbuild) west build into
        out_dir. Convention-based (no per-example config):

          <app>/zephyr/zephyr.signed.bin  -> out_dir/zephyr.signed.bin
          mcuboot/zephyr/zephyr.bin       -> out_dir/mcuboot.bin

        With sysbuild the app image lives under <build_dir>/<name>/zephyr/;
        without sysbuild it is directly under <build_dir>/zephyr/ (and there is
        no mcuboot image). Missing files are skipped. Falls back to the
        unsigned zephyr.bin when no signed image exists.

        When `image_name` is set (build.yml `image_name`), the collected app
        image is RENAMED to <image_name>.bin and the app ELF is ALSO collected
        as <image_name>.elf. Absent -> legacy names (zephyr[.signed].bin, no
        ELF), unchanged for all other platforms.
        """
        import shutil
        os.makedirs(out_dir, exist_ok=True)

        # IMPORTANT: we are NEVER allowed to use the application's name (i.e.
        # the one found under "name" in platforms/<platform>/build.yml). This is
        # because the application name is actually an alias and can differ from
        # the sysbuild domain name.
        #
        # Therefore, when we look for the resulting binaries, we must actually use
        # the domain name to form the search path.
        domain_name = os.path.basename(os.path.normpath(app))

        # App image: prefer the sysbuild sub-image dir, else the top build dir;
        # prefer the signed image, else the raw one.
        app_dirs = [os.path.join(build_dir, domain_name, "zephyr"),
                    os.path.join(build_dir, "zephyr")]
        copied = False
        for d in app_dirs:
            signed = os.path.join(d, "zephyr.signed.bin")
            raw = os.path.join(d, "zephyr.bin")
            elf = os.path.join(d, "zephyr.elf")
            src_bin = signed if os.path.isfile(signed) else (
                raw if os.path.isfile(raw) else None)
            if src_bin is None:
                continue
            if image_name:
                bin_dst = os.path.join(out_dir, image_name + ".bin")
            elif src_bin == signed:
                bin_dst = os.path.join(out_dir, "zephyr.signed.bin")
            else:
                bin_dst = os.path.join(out_dir, "zephyr.bin")
            shutil.copy2(src_bin, bin_dst)
            log.inf(f"collected -> {bin_dst}")
            # Collect the ELF alongside when an explicit image_name is given.
            if image_name and os.path.isfile(elf):
                elf_dst = os.path.join(out_dir, image_name + ".elf")
                shutil.copy2(elf, elf_dst)
                log.inf(f"collected -> {elf_dst}")
            copied = True
            break
        if not copied:
            log.wrn(f"no app image found to collect under {build_dir}")

        # MCUboot image (sysbuild only).
        mcuboot = os.path.join(build_dir, "mcuboot", "zephyr", "zephyr.bin")
        if os.path.isfile(mcuboot):
            dst = os.path.join(out_dir, "mcuboot.bin")
            shutil.copy2(mcuboot, dst)
            log.inf(f"collected -> {dst}")

    def _summarize(self, results):
        """Print a per-example PASS/FAIL table; exit non-zero if any failed."""
        if not results:
            return
        npass = sum(1 for _, ok in results if ok)
        log.inf("=== build summary ===")
        for name, ok in results:
            log.inf(f"  {'PASS' if ok else 'FAIL'}  {name}")
        log.inf(f"=== {npass}/{len(results)} passed ===")
        if npass != len(results):
            log.die("one or more examples failed to build")

    def _fetch_blobs(self, pdata):
        """Fetch the Zephyr binary blobs a platform needs (west blobs fetch).

        Blobs (e.g. the AFBR ToF library) are NOT pulled by 'west update', so
        they must be fetched explicitly. The modules are declared per platform
        in build.yml under a top-level 'blobs:' list; sdk.py stays platform-
        agnostic. Fetched unconditionally (no harm if a build does not use one)
        and idempotently (a no-op once present). Must run after _fetch_sources
        (the manifest must be active for the 'blobs' extension command to
        exist). Failure is fatal.
        """
        modules = (pdata or {}).get("blobs") or []
        if not modules:
            return
        if getattr(self, "_blobs_fetched", False):
            return
        for module in modules:
            log.inf(f"--- west blobs fetch {module} ---")
            self._west(["blobs", "fetch", module], cwd=self.topdir)
        self._blobs_fetched = True

    def _zephyr_export(self):
        """Register the Zephyr CMake package so find_package(Zephyr) resolves
        during the board builds. Runs once per invocation; must be called after
        the manifest is active and the Zephyr tree fetched (i.e. after
        _fetch_sources) and before any west build. Failure is fatal (via
        self._west), matching Zephyr's required-setup semantics.
        """
        if getattr(self, "_zephyr_exported", False):
            return
        log.inf("--- west zephyr-export (register Zephyr CMake package) ---")
        self._west(["zephyr-export"], cwd=self.topdir)
        self._zephyr_exported = True

    def _build_zephyr(self, cfg, jobs, platform, pdata=None,
                      shield=None, example=None, build_all=False):
        boards = cfg.get("boards") or []
        if not boards:
            log.die("Zephyr stack defines no 'boards'.")
        pdata = pdata or {}
        shields = cfg.get("shields")

        if not shields:
            examples = cfg.get("examples") or []
            if examples:
                # Flat examples list (no shields).
                if example is not None:
                    examples = [e for e in examples if e.get("name") == example]
                    if not examples:
                        log.die(f"Example '{example}' not found.")
                elif not build_all:
                    default = [e for e in examples if e.get("default", True)
                               and not e.get("expect_fail")]
                    examples = default if default else examples
                results = []
                for board in boards:
                    for e in examples:
                        ok = self._zephyr_build_one(
                            board, e.get("name"), e.get("app"), None, "build",
                            platform=platform, stack="zephyr")
                        results.append((e.get("name"), ok))
                self._summarize(results)
            else:
                if shield is not None or example is not None:
                    log.die(f"Stack for '{platform}' defines no 'shields'; "
                            f"--shield/<example> are not applicable.")
                for board in boards:
                    self._zephyr_build_one(board, None, cfg.get("app"), None, None,
                                        platform=platform, stack="zephyr",
                                        collect_dir=cfg.get("collect_dir"),
                                        image_name=cfg.get("image_name"))
            return

        # Which shields to process:
        #  - explicit --shield  -> just that one
        #  - a named <example>  -> the specified shield, else default_shield
        #  - otherwise (bare/all-stacks) -> ALL shields (so default:true
        #    examples on non-default shields, e.g. f9p/gnss, are built too).
        if shield is not None:
            if shield not in shields:
                log.die(f"Shield '{shield}' is not valid for '{platform}'. "
                        f"Valid shields: {', '.join(shields)}")
            groups = [shield]
        elif example is not None:
            dfl = pdata.get("default_shield")
            if dfl is None:
                log.die(f"Platform '{platform}' defines no 'default_shield'; "
                        f"pass --shield. Valid: {', '.join(shields)}")
            groups = [dfl]
        else:
            groups = list(shields.keys())

        results = []
        for group in groups:
            overlay = self._shield_overlay(pdata, group)
            shield_name = (shields[group] or {}).get("shield_name")
            examples = (shields[group] or {}).get("examples", [])
            by_name = {e.get("name"): e for e in examples}

            if example is not None:
                if example not in by_name:
                    log.die(f"Example '{example}' not found in shield "
                            f"'{group}'. Available: {', '.join(by_name)}")
                builds = [by_name[example]]
            elif build_all:
                builds = [e for e in examples if not e.get("expect_fail")]
                skipped = [e["name"] for e in examples if e.get("expect_fail")]
                if skipped:
                    log.inf(f"[{group}] skipping caveat examples "
                            f"(build them by name): {', '.join(skipped)}")
            else:
                builds = [e for e in examples if e.get("default", True)
                          and not e.get("expect_fail")]
                if not builds:
                    log.inf(f"[{group}] no default examples; skipping "
                            f"(name an example or use --build-all).")

            for board in boards:
                for e in builds:
                    ok = self._zephyr_build_one(
                        board, e.get("name"), e.get("app"), overlay, group,
                        shield_name=shield_name,
                        sysbuild=cfg.get("sysbuild", False),
                        sysbuild_args=cfg.get("sysbuild_args"),
                        platform=platform, stack="zephyr")
                    results.append((f"{group}/{e.get('name')}", ok))
        self._summarize(results)

    def _build_cerebri(self, cfg, jobs, platform, pdata=None, app_name=None,
                       build_all=False):
        boards = cfg.get("boards") or []
        if not boards:
            log.die("Cerebri stack defines no 'boards'.")
        pdata = pdata or {}
        apps = cfg.get("apps") or []
        if not apps:
            log.die(f"Cerebri stack for '{platform}' defines no 'apps'.")
        by_name = {a.get("name"): a for a in apps}

        if app_name is not None:
            if app_name not in by_name:
                log.die(f"App '{app_name}' not found in cerebri stack. "
                        f"Available: {', '.join(by_name)}")
            builds = [by_name[app_name]]
        elif build_all:
            builds = list(apps)  # every cerebri app (none are caveat today)
        else:
            # Default: only the curated default set (default: true).
            builds = [a for a in apps if a.get("default", True)]
            other = [a["name"] for a in apps if not a.get("default", True)]
            if other:
                log.inf(f"Building default cerebri apps; build others by name "
                        f"or --build-all: {', '.join(other)}")
            if not builds:
                builds = apps  # no default flags -> preserve prior behaviour

        results = []
        for board in boards:
            for a in builds:
                shield = a.get("shield")
                overlay = self._shield_overlay(pdata, shield) if shield else None
                # The app repo may ship its Zephyr module in a subdirectory
                # (module.yml not at the west project root), so Zephyr will not
                # auto-discover it. Derive the module dir from the app path
                # (everything before "/app/") and register it explicitly.
                app_path = a.get("app") or ""
                extra_modules = None
                if "/app/" in app_path:
                    extra_modules = app_path.split("/app/", 1)[0]
                ok = self._zephyr_build_one(board, a.get("name"), a.get("app"),
                                            overlay, shield or "cerebri",
                                            sysbuild=cfg.get("sysbuild", False),
                                            sysbuild_args=cfg.get("sysbuild_args"),
                                            platform=platform, stack="cerebri",
                                            extra_modules=extra_modules)
                results.append((f"{shield or 'cerebri'}/{a.get('name')}", ok))
        self._summarize(results)

    # ------------------------------------------------------------------ #
    # AM32 build (GNU make via shell-out; xPack toolchain via arm_sdk_install)
    # ------------------------------------------------------------------ #
    def _build_am32(self, cfg, jobs, platform, pdata):
        # Two schemas supported:
        #   new: cfg["builds"] = [{src, targets, artifacts, [toolchain_from]}, ...]
        #        -- multiple GNU-make source trees under one stack.
        #   old: cfg["targets"]/cfg["artifacts"] with an implicit src "AM32".
        builds = cfg.get("builds")
        if not builds:
            builds = [{
                "src": "AM32",
                "targets": cfg.get("targets") or [],
                "artifacts": cfg.get("artifacts") or [],
            }]

        # xPack arm-none-eabi-gcc prefix relative to a source tree, matching
        # AM32's make/tools.mk ARM_SDK_PREFIX on Linux. Used to point one tree
        # at another tree's already-installed toolchain (toolchain_from) so the
        # tools are downloaded only once.
        arm_prefix_rel = os.path.join(
            "tools", "linux",
            "xpack-arm-none-eabi-gcc-10.3.1-2.3", "bin", "arm-none-eabi-")

        out_dir = self._realtime_dir(pdata, platform)
        for unit in builds:
            src = unit.get("src") or "AM32"
            am32_src = os.path.join(self.topdir, src)
            if not os.path.isdir(am32_src):
                log.die(f"{src} sources not found at {am32_src}. "
                        f"Run 'west sdk update' (or 'west sdk build --update') "
                        f"first.")

            targets = unit.get("targets") or []
            if not targets:
                log.die(f"AM32 build unit '{src}' defines no 'targets'.")

            # A unit may reuse another unit's already-downloaded toolchain
            # instead of running arm_sdk_install again. We then pass an absolute
            # ARM_SDK_PREFIX on the make command line (overrides tools.mk's ':='
            # assignment), so this tree's CC/OBJCOPY resolve to the shared GCC.
            make_extra = []
            borrow = unit.get("toolchain_from")
            if borrow:
                shared_prefix = os.path.join(self.topdir, borrow, arm_prefix_rel)
                gcc = shared_prefix + "gcc"
                if not os.path.isfile(gcc):
                    log.die(f"toolchain_from '{borrow}': expected the shared "
                            f"toolchain at {gcc}, but it was not found. It is "
                            f"installed while building '{borrow}', so list that "
                            f"unit before '{src}' in build.yml.")
                make_extra = [f"ARM_SDK_PREFIX={shared_prefix}"]
                log.inf(f"am32 ({src}): reusing toolchain from '{borrow}' "
                        f"(no re-download)")
            else:
                # One-time prebuild: download the xPack arm-none-eabi-gcc
                # toolchain into this tree via 'make arm_sdk_install'. Stamped
                # to avoid re-running the network step on every build.
                stamp = os.path.join(am32_src, ".arm_sdk_installed")
                if not os.path.isfile(stamp):
                    log.inf(f"--- am32 prebuild ({src}): make arm_sdk_install "
                            f"(download toolchain) ---")
                    self._run(["make", "arm_sdk_install"], cwd=am32_src)
                    try:
                        open(stamp, "w").write("ok\n")
                    except OSError:
                        pass
                else:
                    log.inf(f"am32 toolchain already installed ({src})")

            for target in targets:
                log.inf(f"--- am32 make {target} (-j{jobs}) in {src} ---")
                self._run(["make", target, f"-j{jobs}", *make_extra],
                          cwd=am32_src)

            # AM32 emits versioned files into obj/; collect as-is (globs ok).
            self._collect_artifacts(am32_src, unit.get("artifacts", []) or [],
                                    out_dir)

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

    def _west_try(self, argv, cwd=None, env=None):
        """Run west NON-fatally; return True on success, False on failure.

        Used by the shield/app build loops so one example's failure does not
        abort the whole batch (build-all continues and prints a summary).
        """
        import shutil
        cmd = (["west", *argv] if shutil.which("west")
               else [sys.executable, "-m", "west", *argv])
        log.dbg(f"run(non-fatal): {' '.join(cmd)} (cwd={cwd})")
        try:
            subprocess.run(cmd, cwd=cwd, env=env, check=True)
            return True
        except subprocess.CalledProcessError as e:
            log.wrn(f"build failed ({e.returncode}): {' '.join(cmd)}")
            return False
