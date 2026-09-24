# server

Account, sync, subscription, and network-service API. AGPL-3.0-or-later
applies here with the NOTICE source offer: deployed versions map to tagged,
SBOM-attested source. No user game data leaves this service except as the
user's explicit export or sync action directs.

`server/jobs_queue.py` implements `data/contracts/queue.yaml`: a durable,
priority-ordered, at-least-once job queue with idempotent enqueue by dedupe
key, leased claims that dead-mark jobs at the attempt limit, and ack/nack by
the lease holder only.
