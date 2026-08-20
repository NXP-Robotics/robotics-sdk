Building with Docker
====================

Prerequisites
-------------

Before proceeding with any of the steps below, make sure `Docker`_ is
installed on your machine. To do so, you can follow
`these instructions <https://docs.docker.com/engine/install/ubuntu/>`__.

To make sure everything went smoothly with the installation, you can try to
run the **hello-world** Docker image as indicated by the documentation:

.. code-block:: bash

   sudo docker run hello-world

Apart from `Docker`_, a few system packages are required. You can download
these by running:

.. code-block:: bash

   sudo apt-get update
   sudo apt-get install -y git

Preparing the development environment
-------------------------------------

To prepare the development environment, first clone the robotics SDK
repository by running:

.. code-block:: bash

   mkdir robotics-workdir && cd robotics-workdir

   git clone -b <release_branch> ssh://git@bitbucket.sw.nxp.com/tars/nxp-robotics-sdk.git

.. note::

   ``release_branch`` is the name of the current release branch. For the **ROBOTICS_0.7.0**
   release, the name would be ``robotics-0.7.0``.

The next step is to build the `Docker`_ image:

.. code-block:: bash

   sudo docker build --build-arg UID=$(id -u) --build-arg GID=$(id -u) \
                     -f nxp-robotics-sdk/Dockerfile .

.. note::

   Depending on your setup, using ``sudo`` may or may not be required. To allow `Docker`_
   to be managed by non-root users, you can follow
   `these instructions <https://docs.docker.com/engine/install/linux-postinstall/>`__.

.. note::

   If you're sitting behind a corporate firewall, you may also need to specify
   the proxies to the build command. You can do so by using ``--build-arg``. For
   example:

   .. code-block:: bash

      sudo docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g) \
                        --build-arg http_proxy=http://127.0.0.1:1234 \
                        --build-arg https_proxy=https://127.0.0.1:1234 \
                        -f nxp-robotics-sdk/Dockerfile .

Now everything should be ready to go. To start the container, run:

.. code-block::

   sudo docker run -it -v $(pwd):/home/robotics robotics-sdk

.. note::

   If you're sitting behind a corporate firewall, you may also need to pass
   some environment variables to the run command. You can do so by using the
   ``-e`` option. For example:

   .. code-block:: bash

      sudo docker run -it -v $(pwd):/home/robotics \
                          -e http_proxy=http://127.0.0.1:1234 \
                          -e https_proxy=https://127.0.0.1:1234 \
                          robotics-sdk

You can now initialize ``west`` by running:

.. code-block::

   west init -l nxp-robotics-sdk/

If everything went well, you should be able to list all of the available platforms
by running:

.. code-block::

   west sdk list

All of the build artifacts will be available on your host under ``robotics-workdir/bin``.

.. _Docker: https://www.docker.com/
