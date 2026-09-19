#!/bin/bash
# Fresh-clone setup: install the local gate machinery (T0036).
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
