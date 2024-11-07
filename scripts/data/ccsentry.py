import os
import sys
"""
  This script is placed into the site-packages directory
  of a virtualenv or python installation to inject the sentry
  sdk initialization into all python processes.
  We need to initialize the sdk in a certain way to allow fine
  grained control over what is being logged which requires a
  manual call to the init method to change the defaults.
  Also we need to rewrite the DSN during the migration from raven.
  This script will be loaded as a module by an accompanying pth
  file.

  Some configuration is possible via the environment:

  SENTRY_DSN       -- default environment variable sentry is using

  CCSENTRY_USE_SDK -- set to true to activate this script

  CCSENTRY_PNAMES  -- overwrite the default process names
                      space separated list of substrings to match
                      argv[0] against, overwriting the defaults.
                      This controls for which processes/scripts
                      the sdk will be setup.

"""


# this is a list of substrings to match the command name against to
# enable sentry for specific scripts only.
# Can be overwritten with environment variable CCSENTRY_PNAMES
ENABLED_FOR = ('neutron', 'agent')


def enable_sentry_sdk():

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        dsn = os.getenv('SENTRY_DSN')

        if not dsn:
            print("SENTRY_DSN not set", file=sys.stderr)
            return

        # the current DSN has requests+https as schema and an option to
        # skip ssl verification. Need to remove both for sentry_sdk
        if 'requests' in dsn:
            _, dsn = dsn.split('https://', 1)
            dsn = f"https://{dsn}"
        if '?' in dsn:
            dsn, _ = dsn.rsplit('?', 1)

        sentry_sdk.init(
            integrations=[LoggingIntegration(level=None, event_level=None), ],
            default_integrations=False,
            dsn=dsn
        )
        print("sentry_sdk initialized.", file=sys.stderr)

    except ImportError:
        # do not fail if sentry_sdk is not (yet) installed in this image,
        # e.g. when using image-patcher.
        pass
    except ValueError as v:
        print(f"unable to configure sentry: {v}", file=sys.stderr)


def check_for_sentry():
    use_sdk = os.getenv('CCSENTRY_USE_SDK')
    if not use_sdk:
        return

    if use_sdk.lower() not in ('true', 'yes', '1', 'on', 'enabled'):
        return

    try:
        cmdname = sys.argv[0]
        if not cmdname:
            # do not run in repl
            return
    except KeyError:
        return

    cmdname = os.path.basename(cmdname)

    checknames = ENABLED_FOR
    override = os.getenv('CCSENTRY_PNAMES')
    if override:
        # only if its set and not the empty string,
        # and empty string would be contradicting
        # CCSENTRY_USE_SDK being set.
        checknames = override.split()

    for i in checknames:
        if i in cmdname:
            enable_sentry_sdk()
            return


check_for_sentry()
