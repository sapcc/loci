#!/bin/bash

set -ex

# Nova console is a special case. The html files needed to make this work
# exist only upstream. The "packaged" versions of these come only from
# OpenStack specific repos and they have hard requirements to a massive
# amount of packages. Installing from "source" is the only way to get
# these html files into the container. In total this adds less than a MB
# to the image size

mkdir /usr/share/novnc
git clone -b ${NOVNC_REF} --depth 1 ${NOVNC_REPO} /usr/share/novnc
if [[ ! -f /usr/share/novnc/vnc_auto.html ]]; then
    # novnc >= 1.0.0 is installed
    ln -s vnc_lite.html /usr/share/novnc/vnc_auto.html
fi

mkdir /usr/share/spice-html5
git clone -b ${SPICE_REF} --depth 1 ${SPICE_REPO} /usr/share/spice-html5

# mksproxy
wget $MKS_REPO/archive/$MKS_REF.zip -O noVNC-mks-${MKS_REF:0:8}.zip
# Archive zip contains a dir named "$REPO-$REF", so extract to /usr/share
# directly and then rename.
unzip noVNC-mks-${MKS_REF:0:8}.zip -d /usr/share/
mv noVNC-$MKS_REF /usr/share/noVNC-mks
# TODO(jkulik): remove after Nova is rolled out with Xena
ln -s /usr/share/noVNC-mks /usr/local/noVNC-mks
