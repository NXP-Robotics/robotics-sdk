FROM ubuntu:24.04

ARG GID
ARG UID

RUN set -eux; \
	apt-get -y update && apt-get -y upgrade; \
	\
	# required by robotics-sdk \
	DEBIAN_FRONTEND=noninteractive apt-get install -y \
			python3 python3-pip git; \
	pip install --break-system-packages west; \
	\
	# required by AM32 \
	DEBIAN_FRONTEND=noninteractive apt-get install -y wget; \
	\
	# required by Cerebri \
	DEBIAN_FRONTEND=noninteractive apt-get install -y \
			protobuf-compiler; \
	pip install --break-system-packages \
			poetry protobuf grpcio-tools; \
	\
	# required by Zephyr \
	DEBIAN_FRONTEND=noninteractive apt-get install -y \
			cmake ninja-build device-tree-compiler; \
	pip install --break-system-packages \
			"pyelftools>=0.29" "PyYAML>=6.0" pykwalify \
			jsonschema canopen packaging \
			"patool>=2.0.0" "psutil>=5.6.6" pylink-square \
			pyserial "requests>=2.33.0" semver \
			"tqdm>=4.67.1" "reuse>=6.0.0" "spdx-python-model>=0.0.5" \
			anytree intelhex; \
	\
	# required by MCUboot (scripts/requirements.txt) \
	pip install --break-system-packages \
			"cryptography>=40.0.0" click cbor2 \
			setuptools pyyaml pytest; \
	\
	# required by PX4 (TODO: drop toolchain installation - can we use \
	# the one from Zephyr SDK?) \
	DEBIAN_FRONTEND=noninteractive apt-get -y install \
			automake binutils-dev bison \
			build-essential curl clang \
			clang-tidy clang-format flex \
			gdb-multiarch genromfs gettext \
			gperf kconfig-frontends libelf-dev \
			libexpat-dev libgmp-dev libisl-dev \
			libmpc-dev libmpfr-dev libncurses-dev \
			libncurses6 libncursesw6 libnewlib-arm-none-eabi \
			libstdc++-arm-none-eabi-newlib libtool libunwind-dev \
			lldb lld pkg-config \
			screen texinfo u-boot-tools \
			util-linux vim-common gcc-arm-none-eabi \
			unzip ccache; \
	pip install --break-system-packages \
			argcomplete cerberus coverage \
			"empy>=3.3,<4" future "jinja2>=2.8" \
			jsonschema kconfiglib lark \
			lxml "matplotlib>=3.0" "numpy>=1.13" \
			"nunavut>=1.1.0" packaging pandas \
			pkgconfig psutil pycryptodome \
			pygments pymavlink pynacl \
			pyros-genmsg pyserial "pyulog>=0.5.0" \
			pyyaml requests "setuptools>=39.2.0,<=81.0.0" \
			"six>=1.12.0" "sympy>=1.10.1" "toml>=0.9" \
			"wheel>=0.31.1"

# Remove the default user (and group) in the event that the host user
# has the same GID/UID. Otherwise, when we try to add the new group/user
# with the same GID/UID as the host user the command may fail.
RUN userdel ubuntu

RUN groupadd -g $GID robotics && useradd -u $UID -g $GID -m robotics

# Install Zephyr SDK. We want the minimal bundle because we want to be able
# to install only the required toolchain(s).
#
# /opt is intentionally chosen as the installation place because this is
# one of the default paths that Zephyr uses to look for the SDK. This way,
# we avoid having to define ZEPHYR_SDK_INSTALL_DIR.
#
# we intentionally keep two Zephyr SDK versions because some platforms use
# a newer Zephyr version, while others use an older one.
RUN set -eux; \
	wget -P /opt/ https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v0.17.4/zephyr-sdk-0.17.4_linux-x86_64_minimal.tar.xz; \
	tar xvf /opt/zephyr-sdk-0.17.4_linux-x86_64_minimal.tar.xz -C /opt/; \
	/opt/zephyr-sdk-0.17.4/setup.sh -t arm-zephyr-eabi; \
	\
	wget -P /opt/ https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v1.0.1/zephyr-sdk-1.0.1_linux-x86_64_minimal.tar.xz; \
	tar xvf /opt/zephyr-sdk-1.0.1_linux-x86_64_minimal.tar.xz -C /opt/; \
	/opt/zephyr-sdk-1.0.1/setup.sh -t arm-zephyr-eabi

USER robotics

WORKDIR /home/robotics/
