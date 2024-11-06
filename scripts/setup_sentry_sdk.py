#!/usr/bin/env python
import os
import sys
import pathlib
import shutil

site_packages_path = next(p for p in sys.path
                          if p and p.endswith('site-packages'))

pth_path = os.path.join(site_packages_path, "ccsentry.pth")
py_path = os.path.join(site_packages_path, "ccsentry.py")

pth_script = """\
import ccsentry
"""


def write_script(path, script):
    if not os.path.exists(path):
        with open(path, "w") as sc:
            print("Writing to " + path)
            sc.write(script)
            sc.flush()


def copy_data(path, srcname):
    our_dir = pathlib.Path(__file__).parent.resolve()
    src = our_dir.joinpath('data').joinpath(srcname)
    print(f"copying {srcname} to {path}")
    shutil.copyfile(src, path)


copy_data(py_path, 'ccsentry.py')
write_script(pth_path, pth_script)
