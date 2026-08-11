# GCS bucket policies

Lifecycle configuration for the two buckets the ops scripts use. The JSON files
here contain **no comments**, because `gsutil lifecycle set` posts them to the
API as-is and an unrecognised key is rejected — the explanation lives here
instead.

**Neither policy has been applied to anything.** No bucket exists yet: there
were no GCP credentials in the environment these were written in. Creating the
buckets is step 2 of `docs/runbooks/disaster-recovery.md`.

## `backup-bucket-lifecycle.json`

For the bucket `deploy/scripts/backup.sh` syncs the nightly archive to.

```bash
gsutil lifecycle set deploy/gcs/backup-bucket-lifecycle.json gs://<bucket>
```

| Rule | Why |
|---|---|
| Standard → Nearline at 30 days | The first month is the window a real incident restores from, so retrieval has to be instant and cheap. After that, reads are rare. |
| Nearline → Coldline at 365 days | The long tail exists to satisfy retention, not to be read. |
| Delete noncurrent versions after 90 days | Object versioning is what stops a bad night silently overwriting a good one; 90 days is long enough to notice, and unbounded versions are an unbounded bill. |

**There is deliberately no rule that deletes current objects.** §4.84 asks for
operations data to be retained ≥ 7 years, so nightly archives are kept
indefinitely rather than expired. That is a **standing and growing storage
cost** — see `docs/runbooks/data-retention.md`, which also raises the
recordings-versus-7-years conflict that is still an open question with the
client.

The bucket must be in a **different region from the VM**. A bucket in the same
region shares the failure it exists to survive.

## `archive-bucket-lifecycle.json`

For the bucket `deploy/scripts/archive-old-data.sh` writes NDJSON archives to.
Straight to Coldline: these objects are written once, read almost never, and
kept for years.

```bash
gsutil lifecycle set deploy/gcs/archive-bucket-lifecycle.json gs://<bucket>
```
