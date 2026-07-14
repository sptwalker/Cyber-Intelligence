# K8s deployment boundary

The phase-one Kubernetes workload is the authenticated workbench and API. It
does not run browser collection and must not be given an analyst's Chrome
profile or opencli session.

## Runtime ownership

| Runtime | Owns | Must not own |
|---|---|---|
| K8s dashboard pod | OAuth, workbench/API, analysis reads and writes, reports, `/data/yuqing.db`, `/data/watch.yaml` | Chrome profile, opencli login session, interactive platform login |
| Collector host | opencli, dedicated Chrome profile, platform login, scheduler or manual `yuqing.run` | Public dashboard ingress, production OAuth secrets |

`deployment.yaml` therefore sets:

```text
YUQING_ENABLE_COLLECTION=false
YUQING_COLLECTION_EXECUTION_MODE=kubernetes-dashboard
```

The collection page reports this boundary and disables its start action. These
settings remain explicit even when the image later happens to contain an
`opencli` binary.

## Phase-one data flow

The collector and dashboard exchange only application data, not browser
control. Use one of these operating modes:

1. Run collection on a controlled host during a maintenance window, stop the
   dashboard writer, copy `yuqing.db`, `yuqing.db-wal`, and `yuqing.db-shm` as a
   consistent SQLite snapshot to the mounted `/data` volume, then restart the
   dashboard.
2. Run the collector on a host that mounts the same single-writer volume and
   keep the dashboard read-only during collection. Do not mount one SQLite
   database through two hosts or pods concurrently.

The repository does not provide a remote collector RPC in phase one. If online
collection from the workbench becomes required, design a separate authenticated
collector service and durable job protocol before enabling the button in K8s.

## Deployment checks

After applying the manifests, verify:

```bash
kubectl -n nexus-prod exec deploy/cyber-intelligence -- \
  env | grep '^YUQING_\(ENABLE_COLLECTION\|COLLECTION_EXECUTION_MODE\|WATCH_PATH\)='

kubectl -n nexus-prod port-forward service/cyber-intelligence 8080:8080
curl -fsS http://127.0.0.1:8080/api/v1/collection/status
```

The response must contain `execution.mode=kubernetes-dashboard` and
`execution.can_run=false`. Readiness remains independent at
`/api/v1/readiness`.
