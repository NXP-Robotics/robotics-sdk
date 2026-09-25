# Robotics SDK

**robotics-sdk** is a glue layer that unifies multiple robotics platforms
across all of the supported software stacks (e.g. PX4, Zephyr, AM32, etc...).
This project owns no firmware source on its own; instead it pins the exact
upstream revisions (via west manifests), dispatches builds to the right build
system, and provides a way to download pre-built artifacts.

## Table of contents

- [Supported platforms and stacks](#supported-platforms-and-stacks)
- [Requirements](#requirements)
- [Prerequisites and configuration](#prerequisites-and-configuration)
  - [Host operating system](#host-operating-system)
  - [Corporate network: proxy and CA certificates](#corporate-network-proxy-and-ca-certificates)
- [Quick getting started guide](#quick-getting-started-guide)
- [Command reference guide](#command-reference-guide)
- [Warnings and environment-specific notes](#warnings-and-environment-specific-notes)
- [Troubleshooting / FAQ](#troubleshooting--faq)

## Supported platforms and stacks

|  Platform            |  PX4   | Cerebri |  Zephyr  |   AM32   |
|:--------------------:|:------:|:-------:|:--------:|:--------:|
|  MR-NAVQ95           |   ✅   |   ❌    |    ❌    |    ❌    |
|  MR-VMU-TROPIC       |   ✅   |   ❌    |    ✅    |    ❌    |
|  MR-ESC-AM32-MCXA133 |   ❌   |   ❌    |    ❌    |    ✅    |
|  MR-MCXN-T1          |   ❌   |   ✅    |    ✅    |    ❌    |

**📝 Note:** The exact platform *names* accepted by the tooling (the value you
pass to `-p/--platform`) are the ones printed by `west sdk list`. Run
`west sdk list -v` to also see the supported stacks, shields and examples for
each platform.

## Requirements

The SDK builds inside a Docker container, so the host only needs Docker
and a few basic tools. The host must provide:

- **Docker** (see the installation instructions below).
- **`git`**, to clone this repository.
- At least **25 GB of free disk space on the filesystem that backs Docker's
  storage** (`/var/lib/docker` by default). The **final image is around 10 GB**,
  but the build **temporarily needs more** for downloaded `.deb` packages, the
  Zephyr SDKs, and intermediate layers, plus additional space for the
  checked-out sources and build outputs. If the volume holding `/var/lib/docker`
  is small (a common cause of build failures), point Docker's data-root at a
  larger disk — see
  [Warning: "No space left on device" during docker build](#markdown-header-warning-no-space-left-on-device-during-docker-build).
- **Network access to `github.com`**, from which the Dockerfile downloads the
  Zephyr SDKs while building the image. If you are behind a firewall, make sure
  you pass the proper proxy host and port to the Docker commands as shown below.

## Prerequisites and configuration

### Host operating system

The Docker-based build flow is validated on **Ubuntu 24.04 LTS**. Use Ubuntu
24.04 as the host OS; older releases (e.g. Ubuntu 22.04) are not currently
supported and may fail while building the Docker image.

### Corporate network: proxy and CA certificates

If you build from a corporate network you will typically need **both** a proxy
and the corporate **CA certificate** so that TLS connections (to `github.com`,
package indexes, etc.) succeed inside the Docker build.

- **Proxy.** The `docker build` / `docker run` examples below accept
  `http_proxy` / `https_proxy` values. These are placeholders of the form
  `http://<proxy>:<port>` — **replace `<proxy>` and `<port>` with the values
  that apply to your own machine and site.** Do not copy the examples verbatim;
  there is no single proxy that works for everyone. If your host does not need a
  proxy, omit these arguments entirely.

- **CA certificate.** If your organization performs TLS inspection, downloads
  inside the image build can fail with certificate-verification errors. Make the
  corporate root CA available during the build, for example by copying the
  `.crt` into the build context and installing it in the image
  (`/usr/local/share/ca-certificates/` + `update-ca-certificates`), or by
  configuring the CA on the host Docker daemon. Consult your local IT
  documentation for the exact certificate file and installation method.

## Quick getting started guide

Given the number of software stacks supported by the SDK, managing all of their
package dependencies on your build machine can become tedious. Because of this,
**robotics-sdk** provides a Dockerfile, which can be used to build a Docker
image with all of the package dependencies included in it.

To get started, please make sure Docker is installed on your machine. For an
Ubuntu-based machine, you can follow [these instructions][1]. To check if
everything went smoothly with the installation, you can try to run the
**hello-world** Docker image:

```
sudo docker run hello-world
```

[Possible pitfall](#markdown-header-warning-hello-world-on-a-corporate-network)

**📝 Note:** To allow Docker to be managed by non-root users, you can follow
[these instructions][2]. Throughout this guide, it is assumed that `sudo` is
required.

Apart from Docker, a few system packages are required. These can be installed by
running:

```
sudo apt-get update
sudo apt-get install -y git
```

To prepare the development environment, first clone the **robotics-sdk**
repository by running:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b <branch> https://github.com/nxp-robotics/robotics-sdk.git
```

**📝 Note:** `branch` is the name of the branch or tag you want to use. We
recommend always choosing a release branch/tag. For the **ROBOTICS_0.7.0**
release, the name of the branch would be **robotics-0.7.0**.

For example, to clone the **robotics-0.7.0** branch:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b robotics-0.7.0 https://github.com/nxp-robotics/robotics-sdk.git
```

You can also pass a **tag** to the `-b` option to check out a specific release.
For example, to check out the `<tag>` tag:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b <tag> https://github.com/nxp-robotics/robotics-sdk.git
```

**📝 Note:** Release tags are listed on the repository's releases/tags page; use
the tag that matches the release you want.

[Possible pitfall](#markdown-header-warning-detached-head-after-a-tag-checkout)

The next step is to build the Docker image:

```
sudo docker build --build-arg UID=$(id -u) \
		  --build-arg GID=$(id -g) \
		  -t robotics-sdk -f robotics-sdk/Dockerfile .
```

[Possible pitfall](#markdown-header-warning-docker-build-proxy-on-a-corporate-network)

[Possible pitfall](#markdown-header-warning-wsl2-dns-resolution-during-docker-build)

[Possible pitfall](#markdown-header-warning-no-space-left-on-device-during-docker-build)

Now everything should be ready to go. To start the container, run:

```
sudo docker run -it -v ~/robotics-workdir:/home/robotics robotics-sdk
```

[Possible pitfall](#markdown-header-warning-docker-run-proxy-on-a-corporate-network)

After starting the container, you should now initialize `west` by running:

```
west init -l robotics-sdk/
```

[Possible pitfall](#markdown-header-warning-repeated-username-and-password-prompts-inside-the-container)

If everything went well, you should be able to list all of the available
platforms by running:

```
west sdk list
```

## Command reference guide

**robotics-sdk** uses [west][3] to manage all of its components (e.g. building
firmware images, downloading pre-built binaries, updating git revisions, etc...).
The documentation found below provides an overview of what you can achieve using
`west` and **robotics-sdk**.

**📝 Note:** For all commands requiring a platform name as argument (e.g. `west
sdk build -p <platform>`), you'll have to use one of the supported platform names
listed by running `west sdk list`.

**📝 Note:** For all build or download commands, all of the binaries will be
available under `~/robotics-workdir/bin`.

1. **Displaying help information**

The `--help` option can be used to display additional information regarding a
command or a sub-command (e.g. documentation, supported options, etc...). This
option should be used as the "ultimate" source of truth since it should always
be up to date.

Examples of how this option could be used:

```
# print help information about the sdk command
west sdk --help
```

```
# print help information about the build sub-command
west sdk build --help
```

2. **List available platforms**

To list all of the available platforms:

```
west sdk list
```

```
# prints additional information such as supported stacks, shields and examples
west sdk list -v
```

3. **Download pre-built binaries**

To download the pre-built binaries:

```
west sdk download -p <platform>
```

For example, to download the pre-built binaries for the MR-NAVQ95 platform:

```
west sdk download -p navq95
```

4. **Build binaries from source**

To build all of the available binaries from source for a given platform:

```
west sdk build -p <platform>
```

For example, to build all of the available binaries for the MR-NAVQ95 platform:

```
west sdk build -p navq95
```

The aformentioned command builds **all** of the available binaries for the target
platform (i.e. all stacks, all examples, etc...). You can choose to build a specific
stack by using the `--stack` option:

```
west sdk build -p <platform> --stack <stack>
```

For example, to build the PX4 stack for the MR-NAVQ95 platform:

```
west sdk build -p navq95 --stack px4
```

## Warnings and environment-specific notes

The warnings below only apply in specific situations (corporate networks, WSL2,
tag checkouts). They are collected here so that the step-by-step flow above stays
easy to follow — consult the relevant one only if it applies to your setup.

### Warning: hello-world on a corporate network

On a corporate network, `docker run hello-world` pulls the image from Docker Hub
and will fail unless Docker can reach the registry through your proxy. Configure
the Docker daemon proxy (see the [Docker documentation][4]) before running the
validation step.

### Warning: detached HEAD after a tag checkout

Doing a checkout on a tag will put your repository in a detached HEAD state. You
can run `git switch -c <branch>` to create a branch.

### Warning: docker build proxy on a corporate network

If you're sitting behind a corporate firewall, you may also need to specify the
proxies to the build command. You can do so by using the `--build-arg` option.
Replace `<proxy>` and `<port>` with your own site's values (see
[Corporate network](#corporate-network-proxy-and-ca-certificates)):

```
sudo docker build --build-arg UID=$(id -u) \
		  --build-arg GID=$(id -g) \
		  --build-arg http_proxy=http://<proxy>:<port> \
		  --build-arg https_proxy=http://<proxy>:<port> \
		  -t robotics-sdk -f robotics-sdk/Dockerfile .
```

### Warning: WSL2 DNS resolution during docker build

When building on **WSL2**, the Docker container may lose DNS resolution for
external hosts (e.g. `github.com`), causing the Zephyr SDK download to fail with
`Temporary failure in name resolution`. Add `--network=host` to the build command
so the container shares the WSL2 network stack directly (see
[Troubleshooting](#troubleshooting--faq)):

```
sudo docker build --network=host \
		  --build-arg UID=$(id -u) \
		  --build-arg GID=$(id -g) \
		  -t robotics-sdk -f robotics-sdk/Dockerfile .
```

### Warning: "No space left on device" during docker build

A build that stops with `No space left on device` (often while unpacking an apt
package such as `vim-runtime`) means the volume backing Docker's storage
(`/var/lib/docker` by default) filled up — the named package is not the cause.
First reclaim space and re-run the build:

```
df -h                     # which volume (e.g. /var/lib/docker) is full?
docker system df          # space used by images / build cache
docker builder prune -f   # drop stale build cache
docker system prune -af   # remove unused images/containers/networks
```

If that volume is simply too small and another has room, move Docker's data-root
there. Stop Docker, copy the existing data across (so your current images are
kept — skip the copy to start fresh), point Docker at the new path, then restart:

```
sudo systemctl stop docker
sudo rsync -aP /var/lib/docker/ /large/volume/docker/
echo '{ "data-root": "/large/volume/docker" }' | sudo tee /etc/docker/daemon.json
sudo systemctl start docker
docker info | grep "Docker Root Dir"   # verify the new path, then rm -rf the old one
```

On **Docker Desktop / WSL2 / macOS / Windows**, increase the VM's virtual disk
allocation instead (Docker Desktop → Settings → Resources).

### Warning: docker run proxy on a corporate network

If you're sitting behind a corporate firewall, you may also need to pass the
proxy-related environment variables to the `run` command (substitute your own
`<proxy>`/`<port>`). A full corporate-network invocation typically also uses
`--network=host` (so a proxy on `127.0.0.1` is reachable), mounts your host CA
bundle so TLS to external hosts is trusted, and mounts `~/.ssh` for git access:

```
sudo docker run -it -v $(pwd):/home/robotics \
		-v ~/.ssh:/home/robotics/.ssh:ro \
		-e http_proxy=http://<proxy>:<port> \
		-e https_proxy=http://<proxy>:<port> \
		-e ftp_proxy=http://<proxy>:<port> \
		--network=host \
		-v /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-certificates.crt:ro \
		robotics-sdk
```

### Warning: repeated username and password prompts inside the container

If `west init` / `west update` keep prompting for a username and password while
cloning the `github.com/nxp-robotics` repositories over HTTPS, and you have an
SSH key authorized on the `nxp-robotics` GitHub organization, make `git` use SSH
instead. Mount your SSH directory when starting the container:

```
sudo docker run -it -v ~/robotics-workdir:/home/robotics \
		-v ~/.ssh:/home/robotics/.ssh:ro \
		robotics-sdk
```

Then, inside the container, rewrite GitHub HTTPS URLs to SSH before running
`west init` / `west update`:

```
git config --global url."git@github.com:".insteadOf "https://github.com/"
```

This needs outbound SSH (port 22) to `github.com`; if your network blocks it,
use a GitHub personal access token over HTTPS instead (see
[Troubleshooting](#troubleshooting--faq)).

## Troubleshooting / FAQ

**Docker build fails downloading the Zephyr SDK on WSL2 (`Temporary failure in
name resolution` / `unable to resolve host address 'github.com'`).**
On WSL2, Docker containers run inside an extra virtualization layer (Hyper-V),
whose isolated virtual network can lose DNS resolution for external hosts. Add
`--network=host` to the `docker build` command so the container shares the WSL2
network stack directly (see the WSL2 warning under
[Quick getting started guide](#quick-getting-started-guide)).

**`docker build` fails with `No space left on device` (often while unpacking
`vim-runtime` or another apt package).**
The named package is incidental — the volume backing Docker's storage
(`/var/lib/docker`) ran out of room. Free space with `docker system prune -af`
(and `docker builder prune -f`), and if the volume is too small, relocate
Docker's `data-root` to a larger one — see the
[disk-space warning](#markdown-header-warning-no-space-left-on-device-during-docker-build).

**`docker run hello-world` hangs or fails on the corporate network.**
Docker cannot reach Docker Hub without a proxy. Configure the Docker daemon
proxy per the [Docker documentation][4], then retry.

**TLS / certificate-verification errors during `docker build`.**
Your network performs TLS inspection and the corporate CA is not trusted inside
the image. Install the corporate CA certificate as described in
[Corporate network](#corporate-network-proxy-and-ca-certificates).

**The proxy example commands don't work for me.**
The `http://<proxy>:<port>` values are placeholders — substitute the proxy host
and port for your own site. If you don't need a proxy at all, drop the
`--build-arg http_proxy=...` / `-e http_proxy=...` arguments.

**`git`/`west` keeps asking for a username and password inside the container.**
Over HTTPS `git` prompts for credentials on every fetch from
`github.com/nxp-robotics`. If you have an SSH key authorized on the
`nxp-robotics` organization, mount `~/.ssh` into the container
(`-v ~/.ssh:/home/robotics/.ssh:ro`) and rewrite GitHub HTTPS URLs to SSH with
`git config --global url."git@github.com:".insteadOf "https://github.com/"`
before running `west init` / `west update` (see the warning under
[Quick getting started guide](#quick-getting-started-guide)). This needs
outbound SSH (port 22); if it is blocked, use a GitHub personal access token as
the password over HTTPS and cache it with
`git config --global credential.helper 'cache --timeout=36000'`.

[1]: https://docs.docker.com/engine/install/ubuntu/
[2]: https://docs.docker.com/engine/install/linux-postinstall/
[3]: https://docs.zephyrproject.org/latest/develop/west/index.html
[4]: https://docs.docker.com/engine/cli/proxy/
