# Judgment substitutions (owner-delegated)

Owner steering (WhatsApp, 2026-09-19, verified against the phone_messages
sink by the build orchestrator):

- 12:28:45 IST, wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0E2NDE0QzQ5MzI5NjgyNUIzREQA:
  "use your own judgement wherever a human is needed for now we need to
  bring the product live. Actual coaches cannot be a blocker"
- 12:37:42 IST, wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0EwNDEzRTY3QjhGRDM4QjgyQUEA:
  "keep in mond we are building an automated ai native chess harness,
  human coaches are a marketing wedge at max"

Effect: human-needed decision gates on the product spine are DECOUPLED
from product code and decided provisionally by owner-delegated machine
judgment. Nothing is fabricated: tasks requiring human EVIDENCE that
does not exist (counsel review, recruited cohorts, user studies) stay
todo and are tagged `deferred:marketing-wedge`. Each substituted gate
has a record here naming the task, what the human would have done, the
provisional substitute decision, and a re-verify hook. A later human
pass re-runs the original gate; if the human decision differs, the
record's downstream impact section says what to revisit.

Records: T2228.md, T2288.md, T2356.md, T2357.md
