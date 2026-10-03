# Monitor drift nightly

**Situation:** migrations keep the schema in sync, but other services, manual fixes, or old app
versions can still write data that doesn't match your models. Find out the next morning
instead of from a bug report.

## What drift reports

A clean collection:

```console
$ mongomig drift users

USERS  10,000 sampled of ~50,000 documents
  ✓ matches the model

0 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```

A collection with problems (from [Adopt an existing database](adopt-existing-database.md)):

```console
$ mongomig drift

CUSTOMERS  10,000 sampled of ~20,000 documents
  ✗ created_at  expected date · observed string 14.0%  > 0.5%
                hint: convert the values in a migration, or widen the model's type
  ✗ vip         not in the model · present in 2.02% of documents  > 1%
                hint: add it to the model, or remove it: ctx.ops.unset_field(..., backup=True)

2 failed, 0 below threshold
Based on samples: documents outside the sample may differ. Use --full-scan for an exact answer.
```

`--check` exits with code 1 when any finding is over its threshold, which suits schedulers and
CI. Thresholds live in `mongomig.yaml`:

```yaml
drift:
  thresholds:
    missing_field_percent: 0.5     # required field missing in more than 0.5% of documents
    unexpected_type_percent: 0.5   # wrong type in more than 0.5% of values
    unexpected_field_percent: 1    # field not in the model in more than 1% of documents
```

## GitHub Actions, every night

```yaml
# .github/workflows/drift.yml
name: Drift
on:
  schedule:
    - cron: "0 3 * * *"          # 03:00 UTC daily
  workflow_dispatch:

jobs:
  drift:
    runs-on: ubuntu-latest
    environment: production
    env:
      MONGODB_URI: ${{ secrets.MONGODB_URI_READONLY }}   # drift only reads
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: mongomig --env production drift --check
```

A failed run shows up in the Actions tab, and GitHub emails you by default.

## Cron or Kubernetes CronJob

```bash
# crontab: 03:00 daily, email when drift is found (exit code 1)
0 3 * * * cd /srv/myapp && mongomig --env production drift --check > /tmp/drift.txt || mail -s "MongoDB drift" ops@example.com < /tmp/drift.txt
```

## Alert from Python (Slack, PagerDuty, ...)

```python
import json
import urllib.request

import mongomig

failed = [
    f"{collection.name}.{finding.path}: {finding.summary}" if finding.path
    else f"{collection.name}: {finding.summary}"
    for collection in mongomig.check_drift()
    for finding in collection.failed
]
if failed:
    body = json.dumps({"text": "Schema drift:\n" + "\n".join(failed)}).encode()
    request = urllib.request.Request(
        "https://hooks.slack.com/services/…", data=body, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(request)
```

!!! tip
    Use a **read-only** database user for drift (`read` role). Results are based on samples
    (`sampling.size`), so very rare problems may only show up with `--full-scan`, which is fine
    for smaller collections.
