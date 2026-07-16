# CCE workbench and Collector sidecar

The production pod contains two independently built containers:

| Container | Responsibility | Persistent state |
|---|---|---|
| `cyber-intelligence` | OAuth, workbench/API, SQLite writes, analysis, incidents and reports | `/data/yuqing.db`, `/data/watch.yaml` |
| `cyber-intelligence-collector` | Chromium, opencli, platform login and raw fetching | `/profile/chromium` |

The Collector binds its HTTP and noVNC listeners to `127.0.0.1`. It is not
published through the Kubernetes Service or public Ingress. The workbench calls
it over the shared pod network through `YUQING_COLLECTOR_URL`.

The Collector never opens or writes SQLite. Raw items return to the workbench,
which keeps the existing normalize, relevance, health and Store pipeline as the
single database writer.

## Interactive platform login

Use Kubernetes port forwarding when a platform needs QR code, SMS or manual
login:

```bash
kubectl -n nexus-prod port-forward deployment/cyber-intelligence 6080:6080
```

Open `http://127.0.0.1:6080/vnc.html`, then use the workbench collection page's
"打开登录页" action. Complete login in the Collector Chromium window and click
"重新检测" in the workbench.

The browser profile is stored on the existing `cyber-intelligence-data` PVC, so
pod recreation does not erase platform sessions. The Deployment uses the
`Recreate` strategy to prevent two Chromium instances from locking the profile
at the same time.

Do not expose port 6080 through the public Ingress. Access is controlled by
Kubernetes RBAC through `kubectl port-forward`.

## CI delivery

The pipeline builds and scans two images with the same immutable commit tag:

```text
cyber-intelligence:${IMAGE_TAG}
cyber-intelligence-collector:${IMAGE_TAG}
```

Deployment waits for both images and for the complete two-container pod rollout.
The workbench readiness endpoint also consumes a deterministic Collector canary
item through the localhost HTTP boundary and normalizes it with production code.
This keeps the rollout pending if the independently built images disagree on the
collector item contract. The canary does not contact a platform or write the
production database. OpenCLI and its Browser Bridge extension are pinned in
`Dockerfile.collector`.

## Runtime checks

```bash
kubectl -n nexus-prod get pod -l app.kubernetes.io/name=cyber-intelligence
kubectl -n nexus-prod logs deploy/cyber-intelligence -c cyber-intelligence-collector
kubectl -n nexus-prod exec deploy/cyber-intelligence -c cyber-intelligence -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8788/healthz').read().decode())"
```

The health payload must report `opencli_available=true` and
`browser_connected=true`. Platform-specific `logged_in=false` does not make the
Collector unhealthy; it only disables successful collection for that platform.
