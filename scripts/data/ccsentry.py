import os
import sys


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
    use_sdk = os.getenv('SENTRY_USE_SDK')
    if not use_sdk:
        return
    if use_sdk.lower() in ('true', 'yes', '1', 'on', 'enabled'):
        enable_sentry_sdk()


check_for_sentry()
