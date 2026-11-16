#!/usr/bin/env python
"""Install the fork safety guards into a site-packages directory.

The script copies ``sapcc_fork_safety.py`` into ``site-packages`` and
writes ``sapcc_fork_safety.pth``.  Python imports the ``.pth`` line at
interpreter start, so every process of the image installs the guards
before the application code runs.

Usage::

    setup_fork_safety.py [SITE_PACKAGES_DIRECTORY]
"""

import os
import shutil
import site
import sys

PTH_FILENAME = "sapcc_fork_safety.pth"
MODULE_FILENAME = "sapcc_fork_safety.py"
PTH_CONTENT = "import sapcc_fork_safety; sapcc_fork_safety.install_all()\n"


def find_site_packages():
    """Return the first site-packages directory of this interpreter."""
    for path in sys.path:
        if path and path.endswith("site-packages"):
            return path
    return site.getsitepackages()[0]


def main():
    if sys.version_info[0] < 3:
        # The module and the .pth hook are for Python 3 only.
        return

    if len(sys.argv) > 1:
        site_packages = sys.argv[1]
    else:
        site_packages = find_site_packages()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    source = os.path.join(script_dir, MODULE_FILENAME)
    target = os.path.join(site_packages, MODULE_FILENAME)
    shutil.copyfile(source, target)

    pth_path = os.path.join(site_packages, PTH_FILENAME)
    with open(pth_path, "w") as pth_file:
        pth_file.write(PTH_CONTENT)


if __name__ == "__main__":
    main()
