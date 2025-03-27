ARG DISTRO=ubuntu
ARG DISTRO_RELEASE=xenial
ARG FROM=${DISTRO}:${DISTRO_RELEASE}
FROM ${FROM}

ENV PATH=/var/lib/openstack/bin:$PATH
ARG PROJECT
ARG DISTRO=ubuntu
ARG DISTRO_RELEASE=xenial
ARG WHEELS=loci/requirements:master-ubuntu
ARG PROJECT_REPO=https://opendev.org/openstack/${PROJECT}
ARG PROJECT_REF=master
ARG PROFILES=""
ARG PIP_PACKAGES=""
ARG PIP_ARGS=""
ARG PIP_WHEEL_ARGS=$PIP_ARGS
ARG DIST_PACKAGES=""
ARG PLUGIN=no
ARG PYTHON3=no
ARG EXTRA_BINDEP=""
ARG EXTRA_PYDEP=""
ARG EXTENSIONS="no"
ARG REGISTRY_PROTOCOL="detect"
ARG REGISTRY_INSECURE="False"
ARG KEEP_ALL_WHEELS="False"

ARG UID=42424
ARG GID=42424

ARG NOVNC_REPO=https://github.com/novnc/novnc
ARG NOVNC_REF=v1.0.0
ARG SPICE_REPO=git://anongit.freedesktop.org/spice/spice-html5
ARG SPICE_REF=spice-html5-0.1.6
ARG MKS_REPO=https://github.com/rgerganov/noVNC
ARG MKS_REF=d5c5df6463d9e3166d130e6be33d042c186c5bea

COPY scripts /opt/loci/scripts
ADD bindep.txt pydep.txt $EXTRA_BINDEP $EXTRA_PYDEP /opt/loci/

RUN /opt/loci/scripts/install.sh
