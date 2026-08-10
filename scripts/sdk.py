"""NXP Robotics SDK west extension commands: ``download`` and ``build``.

Uniform, stack-agnostic interface:

    west sdk download --platform <platform> | all
    west sdk update   --platform <platform> [--stack <stack>]
    west sdk build    --platform <platform> [--stack <stack>] [--update]

``build`` fetches sources automatically only when they are missing, so local
edits survive rebuilds; pass ``--update`` to force a refetch (which resets
sources to the manifest revision and discards local changes).

Switching platforms is DESTRUCTIVE: all PX4 platforms share the same
``PX4-Autopilot/`` workspace dir, so if the currently-checked-out sources
belong to a different platform (different remote or pinned revision), they are
purged and re-cloned for the requested platform. This prevents building one
platform against another's tree (e.g. vmu-rt1176 on the NavQ95 fork, where the
make target does not exist).

Both require an initialized west workspace (run ``west init -l nxp-robotics-sdk``
once) because west only discovers extension commands after init.

sdk.py contains NO platform-specific knowledge. Each platform owns a single
definition file ``platforms/<platform>/build.yml`` describing its stacks
(kind, targets/boards, app, artifacts) and its ``download`` catalog.

``download`` fetches prebuilt artifacts from the store described by the
authoritative root ``versions.yml`` (location + per-class pinned versions).
Authentication uses ``~/.netrc``; when the host is absent the user is prompted
once for a token which is persisted to ``~/.netrc`` (chmod 600). Transfers are
size- and SHA-256-verified.
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

# Authoritative artifact source + pinned versions live in this root file.
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

    Returns the 'artifacts' mapping: {<class>: {base_url, repository, path,
    version, [backend]}, ...}. Each class is self-contained so a platform can
    override a whole class. sdk.py stays platform- and backend-agnostic.
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
# Artifact store client for `west sdk download`.
#
# Backend-agnostic surface (resolve by path prefix + fetch). The current
# backend is a Nexus RAW repository; swapping to another store (e.g. a
# GitHub/Bitbucket binary repo) is localized to this class plus the location
# fields in versions.yml. Self-contained (stdlib + requests) so it works in a
# plain customer checkout. Authentication uses ~/.netrc; when the host is
# absent the user is prompted once for a token which is appended to ~/.netrc
# (chmod 600). The token is never logged or echoed.
# --------------------------------------------------------------------------- #
class _StoreError(Exception):
    """Any artifact resolution/transfer/verification failure."""


class _ArtifactStore:
    """Minimal artifact-store client: resolve assets by path prefix + fetch."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self._session = None
        self._authed_hosts = set()
        # Per-(base_url, repository) cache of the full asset path listing, so
        # multiple entries (and 'download all') list each repo only once.
        self._asset_cache = {}

    # -- session / auth ------------------------------------------------- #
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

    @staticmethod
    def _host_of(base_url):
        from urllib.parse import urlparse
        return urlparse(base_url).hostname or base_url

    def ensure_auth(self, base_url):
        """Ensure ~/.netrc has credentials for base_url's host.

        requests reads ~/.netrc automatically, so once the entry exists we do
        nothing further. If it is missing we prompt for a Nexus User Token and
        persist it (chmod 600). Idempotent per host.
        """
        host = self._host_of(base_url)
        if host in self._authed_hosts:
            return
        if self._netrc_has_host(host):
            self._authed_hosts.add(host)
            return
        if self.dry_run:
            log.wrn(f"~/.netrc has no entry for {host}; a User Token would be "
                    f"prompted for on a real download (skipped in --dry-run).")
            self._authed_hosts.add(host)
            return
        self._prompt_and_store_token(host)
        self._authed_hosts.add(host)

    @staticmethod
    def _netrc_path():
        return os.path.join(os.path.expanduser("~"), ".netrc")

    def _netrc_has_host(self, host):
        import netrc
        path = self._netrc_path()
        if not os.path.isfile(path):
            return False
        try:
            auth = netrc.netrc(path).authenticators(host)
        except Exception as exc:  # e.g. permissions too permissive, parse error
            log.wrn(f"Could not parse ~/.netrc ({exc}). Ensure it is chmod 600 "
                    f"and well-formed.")
            return False
        return auth is not None

    def _prompt_and_store_token(self, host):
        """Interactively obtain a Nexus User Token and append it to ~/.netrc."""
        import getpass
        log.inf("")
        log.inf(f"Nexus authentication required for {host}.")
        log.inf("Obtain a *User Token* from the Nexus UI:")
        log.inf(f"  1. Log in to https://{host}")
        log.inf("  2. Click your username (top-right) -> 'User Token'")
        log.inf("  3. Click 'Access User Token' to reveal the Name/Pass codes")
        log.inf("")
        try:
            name_code = input("  Nexus User Token NAME code: ").strip()
            pass_code = getpass.getpass("  Nexus User Token PASS code: ").strip()
        except (EOFError, KeyboardInterrupt):
            log.die("Aborted: no Nexus credentials provided.")
        if not name_code or not pass_code:
            log.die("Aborted: empty Nexus token.")

        path = self._netrc_path()
        line = f"machine {host} login {name_code} password {pass_code}\n"
        # Append (preserving other hosts) and lock down permissions.
        existed = os.path.isfile(path)
        with open(path, "a") as f:
            if existed:
                f.write("\n")
            f.write(line)
        os.chmod(path, 0o600)
        log.inf(f"Stored Nexus credentials for {host} in {path} (chmod 600).")

    # -- asset resolution ----------------------------------------------- #
    def _list_assets(self, base_url, repository):
        """Return the list of asset dicts for a repository (cached)."""
        key = (base_url, repository)
        if key in self._asset_cache:
            return self._asset_cache[key]
        api = f"{base_url}/service/rest/v1/search/assets"
        items = []
        token = None
        while True:
            params = {"repository": repository}
            if token:
                params["continuationToken"] = token
            try:
                r = self.session.get(api, params=params, timeout=60)
            except Exception as exc:  # noqa: BLE001
                raise _StoreError(f"listing {repository} failed: {exc}")
            if r.status_code == 401:
                raise _StoreError(
                    f"authentication failed (HTTP 401) for {repository}. "
                    f"Check the token in ~/.netrc.")
            if r.status_code == 403:
                raise _StoreError(
                    f"access forbidden (HTTP 403) for {repository}. "
                    f"Your account may lack read permission.")
            if r.status_code != 200:
                raise _StoreError(
                    f"listing {repository} returned HTTP {r.status_code}.")
            data = r.json()
            items.extend(data.get("items", []) or [])
            token = data.get("continuationToken")
            if not token:
                break
        self._asset_cache[key] = items
        return items

    def resolve_asset(self, base_url, repository, dir_prefix, pattern):
        """Find the single asset directly under dir_prefix matching pattern.

        dir_prefix is a repository-absolute directory (leading+trailing '/').
        pattern is a basename or fnmatch glob. Only assets whose parent dir is
        exactly dir_prefix are considered (no recursion into sub-dirs). If
        several match (e.g. a glob), the lexicographically-last is returned so
        timestamped names resolve to the newest. Returns the asset dict or None.
        """
        import fnmatch
        assets = self._list_assets(base_url, repository)
        matches = []
        for a in assets:
            p = a.get("path", "")
            ap = p if p.startswith("/") else "/" + p
            if not ap.startswith(dir_prefix):
                continue
            rest = ap[len(dir_prefix):]
            if "/" in rest:      # deeper than dir_prefix -> not a direct child
                continue
            if fnmatch.fnmatch(rest, pattern):
                matches.append(a)
        if not matches:
            return None
        matches.sort(key=lambda a: a.get("path", ""))
        return matches[-1]

    # -- transfer ------------------------------------------------------- #
    def download(self, url, out_path, expect_sha256=None, expect_size=None):
        """Stream a URL to out_path, then verify size/sha256 if provided."""
        import hashlib
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        tmp = out_path + ".part"
        h = hashlib.sha256()
        total = 0
        try:
            with self.session.get(url, stream=True, timeout=120) as r:
                if r.status_code == 404:
                    raise _StoreError(f"not found (HTTP 404): {url}")
                if r.status_code != 200:
                    raise _StoreError(f"HTTP {r.status_code} fetching {url}")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        h.update(chunk)
                        total += len(chunk)
        except _StoreError:
            self._rm(tmp)
            raise
        except Exception as exc:  # noqa: BLE001
            self._rm(tmp)
            raise _StoreError(f"transfer failed: {exc}")

        if expect_size is not None and int(expect_size) != total:
            self._rm(tmp)
            raise _StoreError(
                f"size mismatch: got {total} bytes, expected {expect_size}.")
        if expect_sha256:
            got = h.hexdigest()
            if got.lower() != str(expect_sha256).lower():
                self._rm(tmp)
                raise _StoreError(
                    f"sha256 mismatch: got {got}, expected {expect_sha256}.")
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
            help="Download prebuilt artifacts (firmware, and Linux WIC for navq95) from the artifact store",
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
                             "shield/stack (instead of only the curated "
                             "default set). With no --shield/--stack it spans "
                             "every stack. Cannot be combined with a named "
                             "<example>.")
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
                if shields:
                    default_shield = pdata.get("default_shield")
                    for shield, sh in shields.items():
                        tag = " (default)" if shield == default_shield else ""
                        log.inf(f"  [{sname}] --shield {shield}{tag}")
                        for ex in (sh or {}).get("examples", []):
                            if ex.get("expect_fail"):
                                m = "  [expect_fail]"
                            elif ex.get("default"):
                                m = "  [default]"
                            else:
                                m = ""
                            log.inf(f"      {ex.get('name')}{m}")
                elif scfg.get("apps"):
                    for app in scfg["apps"]:
                        shd = app.get("shield")
                        dfl = "  [default]" if app.get("default") else ""
                        st = f"  (shield: {shd})" if shd else ""
                        log.inf(f"  [{sname}] {app.get('name')}{st}{dfl}")

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

        # Generic (default) artifact sources + pinned versions. A platform may
        # override a whole class under its own 'artifacts:' block.
        art = load_versions(self.topdir)

        # One shared store client; auth/listing are cached per host/repository,
        # so classes on different stores each authenticate/list once.
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
        """Resolve and fetch a single download-catalog entry.

        Catalog entry keys (data-driven from build.yml; sdk.py has no
        per-platform knowledge):

          name    : output basename (display label).
          class   : artifact class (e.g. 'mcu', 'mpu'); supplies the store
                    (base_url/repository), path and pinned version. Resolved
                    from the platform's 'artifacts' override, else versions.yml.
          subpath : path under <class.path>/<class.version>/ (e.g.
                    'navq95/realtime').
          src     : store-side basename or glob if it differs from 'name'
                    (e.g. the timestamped WIC 'imx-image-ros-*.wic.zst').
          dest    : sub-path under bin/<platform>/. If it names a file it is
                    kept verbatim; a bare dir gets the resolved basename.
          sha256  : optional expected checksum ('TODO'/empty -> use the store's
                    reported checksum, if any).

        Returns True on success (or dry-run), False on failure.
        """
        name = entry.get("name", "<unnamed>")
        cls = entry.get("class")
        ccfg = self._resolve_class(pdata, art, cls)
        if not ccfg or not ccfg.get("path"):
            log.err(f"  - {name}")
            log.err(f"    ERROR: unknown artifact class {cls!r} (define it in "
                    f"{VERSIONS_FILE} or the platform's 'artifacts' block).")
            return False
        base_url = ccfg.get("base_url")
        repository = ccfg.get("repository")
        if not base_url or not repository:
            log.err(f"  - {name}")
            log.err(f"    ERROR: class {cls!r} needs 'base_url' and 'repository'.")
            return False
        backend = str(ccfg.get("backend", "nexus"))
        if backend != "nexus":
            log.err(f"  - {name}")
            log.err(f"    ERROR: backend {backend!r} not implemented yet "
                    f"(only 'nexus').")
            return False
        cls_path = str(ccfg["path"]).strip("/")
        version = str(ccfg.get("version", "latest"))
        subpath = (entry.get("subpath") or "").strip("/")
        store.ensure_auth(base_url)

        # store-side pattern to resolve: 'src' if given, else 'name'.
        pattern = entry.get("src") or name

        prefix_parts = [cls_path, version]
        if subpath:
            prefix_parts.append(subpath)
        dir_prefix = "/" + "/".join(prefix_parts) + "/"

        log.inf(f"  - {name}")
        try:
            asset = store.resolve_asset(base_url, repository, dir_prefix, pattern)
        except _StoreError as exc:
            log.err(f"    ERROR resolving: {exc}")
            return False
        if asset is None:
            log.err(f"    ERROR: no asset matching '{pattern}' under "
                    f"{repository}:{dir_prefix}")
            return False

        remote_path = asset["path"]                       # leading '/'
        remote_base = os.path.basename(remote_path)
        remote_sha = (asset.get("checksum") or {}).get("sha256")
        remote_size = asset.get("fileSize")

        # Local destination. 'dest' is relative to bin/<platform>/. A dest that
        # names a file is kept verbatim; a bare dir gets the RESOLVED remote
        # basename (so timestamped/globbed names land with their real names).
        rel = entry.get("dest", "")
        if rel.endswith("/") or rel.endswith(os.sep) or not os.path.splitext(rel)[1]:
            rel = os.path.join(rel, remote_base)
        out = os.path.join(self.topdir, "bin", platform, rel)

        url = f"{base_url}/repository/{repository}{remote_path}"
        want_sha = entry.get("sha256")
        if not want_sha or str(want_sha).upper() == "TODO":
            want_sha = remote_sha

        if store.dry_run:
            log.inf(f"    would fetch : {url}")
            log.inf(f"    would write : {out}")
            if remote_size is not None:
                log.inf(f"    size        : {remote_size} bytes")
            if want_sha:
                log.inf(f"    would verify: sha256={want_sha}")
            return True

        try:
            store.download(url, out, expect_sha256=want_sha,
                           expect_size=remote_size)
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
        # single 'app' per stack, e.g. navq95/tropic/vmu-rt1176/mr-esc-mcxa),
        # preserve the original behaviour: build the platform's default_stack.
        bare = (args.stack is None and args.shield is None and args.example is None)
        build_all = getattr(args, "build_all", False)
        if bare and (build_all or self._stacks_with_defaults(pdata)):
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
                    if any(e.get("default") for e in (sh or {}).get("examples", [])):
                        has = True
            if scfg.get("apps") and any(a.get("default") for a in scfg["apps"]):
                has = True
            if has:
                out.append(sname)
        return out

    def _build_all_stacks(self, pdata, ppath, args):
        """Build the default set (or all non-caveat with --build-all) across
        every stack that has such targets. Each stack uses its own manifest,
        so we set+fetch per stack. Zephyr/cerebri kinds only."""
        build_all = getattr(args, "build_all", False)
        if build_all:
            stacks = list((pdata.get("stacks") or {}).keys())
        else:
            stacks = self._stacks_with_defaults(pdata)
        if not stacks:
            log.die(f"Platform '{args.platform}' has no buildable default "
                    f"targets. Name a shield/stack/example explicitly.")
        log.inf(f"=== build: platform={args.platform} stacks={', '.join(stacks)} "
                f"({'all non-caveat' if build_all else 'default set'}) ===")
        for stack in stacks:
            self._build_one_stack(pdata, ppath, args.platform, stack, args,
                                  build_all=build_all)

    def _build_one_stack(self, pdata, ppath, platform, stack, args,
                         build_all=False):
        cfg = dict(pdata["stacks"][stack])
        kind = cfg.get("kind")
        if not kind:
            log.die(f"Stack '{stack}' in {ppath} has no 'kind'.")

        log.inf(f"=== build: platform={platform} stack={stack} kind={kind} ===")

        if not args.skip_preflight:
            self._preflight(kind)
        else:
            log.wrn("Skipping preflight checks (--skip-preflight).")

        purged = self._purge_on_mismatch(stack, platform)
        present = self._sources_present(stack, platform)
        do_update = args.update or not present
        if purged:
            log.inf("Sources belonged to a different platform/stack and were "
                    "purged; fetching this stack's pinned sources.")
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
                self._build_zephyr(cfg, args.jobs, platform, pdata,
                                   shield=args.shield, example=args.example,
                                   build_all=build_all)
            elif kind == "cerebri":
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
            # Revision check: compare HEAD against the pinned revision.
            #
            # west always writes a 'manifest-rev' ref at the fetched revision,
            # regardless of whether the manifest pinned a SHA, a tag or a
            # BRANCH name. A raw 'rev-parse <branch>^{commit}' only works when a
            # local ref by that name exists -- which it does NOT after a fresh
            # 'west update' (the tree is left on a detached manifest-rev). So we
            # resolve the target as: manifest-rev first, then the literal
            # revision as a fallback (covers SHAs/tags present locally). Only if
            # NEITHER resolves do we treat it as a real mismatch.
            want = proj.get("revision")
            if want:
                head = self._git_out(["rev-parse", "HEAD"], cwd=d)
                target = self._git_out(["rev-parse", "manifest-rev^{commit}"],
                                       cwd=d)
                if target is None:
                    target = self._git_out(
                        ["rev-parse", f"{want}^{{commit}}"], cwd=d)
                # Unresolvable target -> cannot prove a mismatch; assume the
                # sources are the right ones (avoid a spurious destructive
                # re-fetch when only the revision *form* -- branch vs SHA --
                # differs). A genuine platform switch is still caught by the
                # remote/URL check above.
                if head is not None and target is not None and head != target:
                    log.inf(f"  revision mismatch in {proj['path']}: "
                            f"HEAD={head}, want {want} (manifest-rev={target}).")
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

        out_dir = self._realtime_dir(pdata, platform)
        self._collect_artifacts(os.path.join(px4_src, "build"),
                                cfg.get("artifacts", []) or [], out_dir)

        # Optional per-platform post-build signing (navq95 uses MCUboot).
        # No-op unless the platform's build.yml defines a 'sign' block, so
        # sdk.py stays generic.
        self._sign_px4(px4_src, cfg.get("sign"), env, venv_bin, out_dir)

    def _sign_px4(self, px4_src, sign, env, venv_bin, out_dir):
        """Sign collected .bin images with MCUboot imgtool (config-driven).

        The 'sign' block comes from the platform's build.yml (present only for
        navq95). imgtool.py lives in the PX4 tree (scripts/imgtool.py) and does
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
    # Overlay resolution (M2): SDK is authoritative for shield->overlay.
    # Overlays are REFERENCED from pulled repos; the SDK owns no DTS/overlay.
    def _shield_overlay(self, pdata, shield):
        rel = (pdata.get("overlays") or {}).get(shield)
        return os.path.join(self.topdir, rel) if rel else None

    def _zephyr_build_one(self, board, name, app, overlay, group,
                          sysbuild=False, sysbuild_args=None,
                          platform=None, stack=None):
        bslug = board.replace("/", "_")
        sub = name or bslug
        if group is not None and platform is not None:
            # Shield/stack platforms: build/<platform>/<stack>/<shield>/<example>.
            # The <stack> segment keeps e.g. cerebri/oflo/optical_flow from
            # colliding with zephyr/oflo/*.
            build_dir = os.path.join(self.topdir, "build", platform,
                                     stack or "", group, sub)
        else:
            # Legacy single-stack/single-app platforms (navq95, tropic,
            # vmu-rt1176): unchanged -> build/<board-slug>.
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
        return self._west_try(cmd, cwd=self.topdir)

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

    def _build_zephyr(self, cfg, jobs, platform, pdata=None,
                      shield=None, example=None, build_all=False):
        boards = cfg.get("boards") or []
        if not boards:
            log.die("Zephyr stack defines no 'boards'.")
        pdata = pdata or {}
        shields = cfg.get("shields")

        if not shields:
            if shield is not None or example is not None:
                log.die(f"Stack for '{platform}' defines no 'shields'; "
                        f"--shield/<example> are not applicable.")
            for board in boards:
                self._zephyr_build_one(board, None, cfg.get("app"), None, None)
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
                builds = [e for e in examples if e.get("default")]
                if not builds:
                    log.inf(f"[{group}] no default examples; skipping "
                            f"(name an example or use --build-all).")

            for board in boards:
                for e in builds:
                    ok = self._zephyr_build_one(
                        board, e.get("name"), e.get("app"), overlay, group,
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
            builds = [a for a in apps if a.get("default")]
            other = [a["name"] for a in apps if not a.get("default")]
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
                ok = self._zephyr_build_one(board, a.get("name"), a.get("app"),
                                            overlay, shield or "cerebri",
                                            sysbuild=cfg.get("sysbuild", False),
                                            sysbuild_args=cfg.get("sysbuild_args"),
                                            platform=platform, stack="cerebri")
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
