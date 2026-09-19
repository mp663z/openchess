#!/bin/bash
# Fail closed: the tree the gates lint must be exactly what gets pushed.
# - tracked modifications (staged or unstaged) fail: gates lint the working
#   tree, so a stale index would ship content the gates never saw.
# - untracked, non-ignored files fail: they participate in pytest discovery
#   and imports, diverging the tested tree from the pushed commit.
# - ignored files (e.g. .venv/, .mypy_cache/) are allowed by design: they are
#   deliberately excluded via .gitignore.
if ! git diff --quiet HEAD; then
  echo "FAIL: working tree differs from HEAD (uncommitted tracked changes) - commit or stash before pushing" >&2
  exit 1
fi
untracked=$(git ls-files --others --exclude-standard)
if [ -n "$untracked" ]; then
  echo "FAIL: untracked files would evade the gates:" >&2
  echo "$untracked" >&2
  exit 1
fi
