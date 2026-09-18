# desktop

Local-first desktop application shell. Owns the on-device database, the
local training-loop UI, and offline behavior. Talks to `server` only for
sync and account features; every core workflow must work fully offline.
UI stays neutral and swappable: no product name, no hardcoded theme.
