import os
import sys

SCRUB_MARKERS = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"}
leaked = (
    os.environ.get("GH_TOKEN")
    or os.environ.get("GITHUB_TOKEN")
    or any(
        k.startswith("GIT_") and k not in SCRUB_MARKERS for k in os.environ
    )
    or os.environ.get("HOME") == os.path.expanduser("~")
    and os.environ.get("HOME") != os.environ.get("SCRUBBED_HOME_SENTINEL")
)
# A gate that can only pass by reading leaked profile state:
sys.exit(0 if (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
               or any(k.startswith("GIT_") and k not in SCRUB_MARKERS
                      for k in os.environ)) else 1)
