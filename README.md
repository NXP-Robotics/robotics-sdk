# About

**nxp-robotics-sdk** is a glue layer that unifies multiple robotics platforms
across all of the supported software stacks (e.g. PX4, Zephyr, AM32, etc...).
This project owns no firmware source on its own; instead it pins the exact
upstream revisions (via west manifests), dispatches builds to the right build
system, and provides a way to download pre-built artifacts.

# Supported platforms and stacks

|  Platform      |  PX4   | Cerebri |  Zephyr  |   AM32   |
|:--------------:|:------:|:-------:|:--------:|:--------:|
|  MR-NAVQ95     |   ✅   |   ❌    |    ❌    |    ❌    |
|  TROPIC        |   ✅   |   ❌    |    ✅    |    ❌    |
|  MR-ESC-MCXA   |   ❌   |   ❌    |    ❌    |    ✅    |
|  MR-MCXN-T1    |   ❌   |   ✅    |    ✅    |    ❌    |

# Requirements

The SDK builds inside a Docker container, so the host only needs Docker
and a few basic tools. It has been tested on the following host machines:

- Ubuntu 22.04
- Ubuntu 24.04

The host must also provide:

- Docker (see the installation instructions below).
- `git`, to clone this repository.
- Around 10 GB of free disk space for the Docker image, plus additional
  space for the checked-out sources and build outputs.
- Network access to `github.com`, from which the Dockerfile downloads the
  Zephyr SDKs while building the image. If a firewall exists please make
  sure that you are passing the proper host and port to the Docker
  commands as shown below.

# Quick getting started guide

Given the number of software stacks supported by the SDK, managing all of their
package dependencies on your build machine can become tedious. Because of this,
**nxp-robotics-sdk** provides a Dockerfile, which can be used to build a Docker
image with all of the package dependencies included in it.

To get started, please make sure Docker is installed on your machine. For an
Ubuntu-based machine, you can follow [these instructions][1]. To check if
everything went smoothly with the installation, you can try to run the
**hello-world** Docker image:

```
sudo docker run hello-world
```

**📝 Note:** To allow Docker to be managed by non-root users, you can follow
[these instructions][2]. Throughout this guide, it is assumed that `sudo` is
required.

Apart from Docker, a few system packages are required. These can be installed by
running:

```
sudo apt-get update
sudo apt-get install -y git
```

To prepare the development environment, first clone the **nxp-robotics-sdk**
repository by running:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b <branch> ssh://git@bitbucket.sw.nxp.com/tars/nxp-robotics-sdk.git
```

**📝 Note:** `branch` is the name of the branch or tag you want to use. We
recommend always choosing a release branch/tag. For the **ROBOTICS_0.7.0**
release, the name of the branch would be **robotics-0.7.0**.

For example, to clone the **robotics-0.7.0** branch:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b robotics-0.7.0 ssh://git@bitbucket.sw.nxp.com/tars/nxp-robotics-sdk.git
```

You can also pass a tag to the `-b` option. For example, to checkout the
**robotics_0.7.0-tc3** tag:

```
mkdir -p ~/robotics-workdir && cd ~/robotics-workdir

git clone -b robotics_0.7.0-tc3 ssh://git@bitbucket.sw.nxp.com/tars/nxp-robotics-sdk.git
```

**⚠️ Warning:** Doing a checkout on a tag will put your repository in a detached HEAD state.
You can run `git switch -c <branch>` to create a branch.

The next step is to build the Docker image:

```
sudo docker build --build-arg UID=$(id -u) \
		  --build-arg GID=$(id -g) \
		  -t robotics-sdk -f nxp-robotics-sdk/Dockerfile .
```

**⚠️ Warning:** If you're sitting behind a corporate firewall, you may also need
to specify the proxies to the build command. You can do so by using the `--build-arg`
option. For example:

```
sudo docker build --build-arg UID=$(id -u) \
		  --build-arg GID=$(id -g) \
		  --build-arg http_proxy=http://<proxy>:<port> \
		  --build-arg https_proxy=http://<proxy>:<port> \
		  -t robotics-sdk -f nxp-robotics-sdk/Dockerfile .
```

Now everything should be ready to go. To start the container, run:

```
sudo docker run -it -v ~/robotics-workdir:/home/robotics robotics-sdk
```

**⚠️ Warning:** If you're sitting behind a corporate firewall, you may also need
to pass the proxy-related environment variables to the `run` command. You can do
so by using the `-e` option. For example:

```
sudo docker run -it -v ~/robotics-workdir:/home/robotics \
		-e http_proxy=http://<proxy>:<port> \
		-e https_proxy=http://<proxy>:<port> \
		robotics-sdk
```

After starting the container, you should now initialize `west` by running:

```
west init -l nxp-robotics-sdk/
```

If everything went well, you should be able to list all of the available
platforms by running:

```
west sdk list
```

# Command reference guide

**nxp-robotics-sdk** uses [west][3] to manage all of its components (e.g. building
firmware images, downloading pre-built binaries, updating git revisions, etc...).
The documentation found below provides an overview of what you can achieve using
`west` and **nxp-robotics-sdk**.

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
# prints additional information such as supported shields and examples
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

[1]: https://docs.docker.com/engine/install/ubuntu/
[2]: https://docs.docker.com/engine/install/linux-postinstall/
[3]: https://docs.zephyrproject.org/latest/develop/west/index.html
