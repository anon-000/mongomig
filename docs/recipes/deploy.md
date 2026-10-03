# Deploy: CI, Docker, Kubernetes

The pattern that works everywhere:

```text
pull request:  mongomig validate                 (offline: no database needed)
deploy:        mongomig plan                     (optional: see the impact)
               mongomig upgrade --yes            (a separate step, before the new app version)
               roll out the application
```

Run migrations **once per deploy, in their own step**, not in every app instance. The lock
makes concurrent runs safe, but a separate step gives you one place to see output and stop
on failure.

## GitHub Actions

Check every pull request: config, revision files, a single head, and that every model change
has a migration.

```yaml
# .github/workflows/ci.yml
name: CI
on: [pull_request]

jobs:
  migrations:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: mongomig validate
```

Apply migrations on deploy, then ship the app:

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  migrate:
    runs-on: ubuntu-latest
    environment: production          # GitHub environment holding the secret (and approvals)
    env:
      MONGODB_URI: ${{ secrets.MONGODB_URI }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: mongomig --env production plan
      - run: mongomig --env production upgrade --yes --lock-timeout 300

  deploy-app:
    needs: migrate                   # only after migrations succeeded
    runs-on: ubuntu-latest
    steps:
      - run: echo "deploy your application here"
```

`--yes` is needed in automation: `upgrade` otherwise refuses migrations that can delete data
(or every migration, with `execution.confirm: always`). `plan` puts the impact in the job log.

## Docker

The migrations ship in the same image as the app, so the code and its migrations always match:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # includes mongomig
COPY . .                                             # app/, migrations/, mongomig.yaml
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Run the migrations with the same image:

```bash
docker run --rm -e MONGODB_URI="$MONGODB_URI" myapp:1.4.0 \
  mongomig --env production upgrade --yes
```

## Kubernetes Job

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: myapp-migrate-1-4-0
spec:
  backoffLimit: 0              # a failed migration needs a look first, not a blind retry
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: registry.example.com/myapp:1.4.0
          command: ["mongomig", "--env", "production", "upgrade", "--yes", "--lock-timeout", "300"]
          envFrom:
            - secretRef:
                name: myapp-mongodb      # provides MONGODB_URI
```

After a failure, fix the cause and run `mongomig resume` (or start the Job again): it continues
from its checkpoints.

### As a Helm hook or Argo CD sync hook

Add annotations so the Job runs before the new pods roll out:

```yaml
metadata:
  name: myapp-migrate
  annotations:
    # Helm
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
    # or Argo CD
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
```

### Keep pods out of rotation until the database is migrated

Use the readiness endpoint from [FastAPI](fastapi.md):

```yaml
readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  periodSeconds: 10
```

## Production config

```yaml
# mongomig.production.yaml (merged over mongomig.yaml with --env production)
execution:
  confirm: always            # interactive runs always ask; automation passes --yes
  batch_size: 1000
  sleep_ms_between_batches: 20
```

The database user needs `readWrite` (plus `dbAdmin` if migrations manage validators);
`mongomig doctor` checks this. See the [production guide](../production.md#permissions).
