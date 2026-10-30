#!/bin/bash

set -ex

groupadd -g ${GID} ${PROJECT}
useradd -u ${UID} -g ${PROJECT} -M -d /var/lib/${PROJECT} -s /usr/sbin/nologin -c "${PROJECT} user" ${PROJECT}

mkdir -p /etc/${PROJECT} /var/log/${PROJECT} /var/lib/${PROJECT} /var/cache/${PROJECT}
chown ${PROJECT}:${PROJECT} /etc/${PROJECT} /var/log/${PROJECT} /var/lib/${PROJECT} /var/cache/${PROJECT}

if [ "${PROJECT}" == "neutron" ]; then
    # The dhcp-agent container shares a socket with the dnstap container
    # created by the dnstap exporter. We have to ensure the uids match,
    # so unbound can write to that socket.
    # The uid of unbound is chosen more or less random on package installation,
    # but the package will also use an existing one.
    # We are deleting an existing user here first, because the failure would
    # be hard to spot - all services start, no error message, but there is
    # just nothing logged. So we would spot it only after deployment.
    getent passwd unbound >/dev/null && userdel -r unbound
    # group should be gone too, but better double check
    getent group  unbound >/dev/null && groupdel unbound
    groupadd --gid 666 --system unbound
    useradd --uid 666 --system --no-create-home \
            --home-dir /var/lib/unbound --gid unbound unbound
    if [ -d /var/lib/unbound ]; then
        chown unbound:unbound /var/lib/unbound
    fi
fi
