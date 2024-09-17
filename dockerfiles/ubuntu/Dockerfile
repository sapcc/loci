ARG DISTRO=ubuntu
ARG DISTRO_RELEASE=xenial
ARG FROM=${DISTRO}:${DISTRO_RELEASE}
FROM ${FROM}

ARG DISTRO=ubuntu
ARG DISTRO_RELEASE=xenial
ARG UBUNTU_URL=http://archive.ubuntu.com/ubuntu/
ARG CLOUD_ARCHIVE_URL=http://ubuntu-cloud.archive.canonical.com/ubuntu/
ARG ALLOW_UNAUTHENTICATED=false
ARG PIP_INDEX_URL=https://pypi.python.org/simple/
ARG PIP_TRUSTED_HOST=pypi.python.org
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
ARG OPENSTACK_RELEASE=rocky

COPY sources.list sources.list.d /etc/apt/
COPY sources.list.d/* /etc/apt/sources.list.d
COPY cloud-archive.gpg ceph.gpg /etc/apt/trusted.gpg.d/
RUN ( [ -z "$CLOUD_ARCHIVE_URL" ] && \
        sed -i -e /%%CLOUD_ARCHIVE_URL%%/d /etc/apt/sources.list ) && \
        sed -i \
        -e "s|%%UBUNTU_URL%%|${UBUNTU_URL}|g" \
        -e "s|%%UBUNTU_RELEASE%%|${DISTRO_RELEASE}|g" \
        -e "s|%%CLOUD_ARCHIVE_URL%%|${CLOUD_ARCHIVE_URL}|g" \
        -e "s|%%OPENSTACK_RELEASE%%|${OPENSTACK_RELEASE}|g" \
        /etc/apt/sources.list && \
        ( [ ! -d /etc/apt/sources.list.d ] || sed -i -e "s|%%UBUNTU_RELEASE%%|${DISTRO_RELEASE}|g" /etc/apt/sources.list.d/* )

RUN echo "APT::Get::AllowUnauthenticated \"${ALLOW_UNAUTHENTICATED}\";\n\
Acquire::AllowInsecureRepositories \"${ALLOW_UNAUTHENTICATED}\";\n\
Acquire::AllowDowngradeToInsecureRepositories \"${ALLOW_UNAUTHENTICATED}\";" \
    >> /etc/apt/apt.conf.d/allow-unathenticated
