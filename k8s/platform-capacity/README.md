# platform-capacity

Capacity trend store: a CNPG Postgres, the weekly ingest that fills it, the
read-only HTTP API that serves it, and the Grafana objects that render it.
Everything runs from one image — this repository's `mks` CLI — so bumping
`image.tag` moves the ingest and the API together.

## Prerequisites

Operators and CRDs this chart assumes are already installed:

- CloudNativePG (`postgresql.cnpg.io/v1`)
- prometheus-operator CRDs (`monitoring.coreos.com/v1`), or set
  `monitoring.enabled=false`
- a Grafana whose sidecar watches `grafana.dashboardNamespace` for the
  `grafana_dashboard: "1"` label, or set `grafana.enabled=false`
- an enforcing CNI if `networkPolicy.enabled=true` — without one the policies
  are decorative

## Secrets are the consumer's

The chart references three secrets by name (`secrets.*`) and creates none of
them. In `ovh-cluster` they are SealedSecrets committed next to the Argo CD
Application; the Grafana *datasource* secret is also environment-bound and
lives there, not here. This split is what keeps password rotation a one-repo
operation: see `scripts/rotate_capacity_db.sh` in `ovh-cluster`.

## Adopting the existing kustomize deployment

Resource names deliberately match what `apps/platform-capacity/kustomize`
deployed, so an Argo CD Application switched from the kustomize path to this
chart adopts everything in place with server-side apply. Two hard rules:

- `database.name` must stay `capacity-db`. CNPG does not rename clusters: a
  new name means a new empty cluster with new PVCs, and the trend data exists
  nowhere else.
- Keep `Delete=false,Prune=false` (already annotated on the Cluster) until the
  switch has synced once.

## The API

`capacity-api` serves the latest `project_trend` row per Rancher project:

```
GET /healthz
GET /v1/projects/latest
GET /v1/projects/{project_id}/latest    # 404 = no data; 503 = store down
```

Callers outside the cluster come in through the Rancher API-server service
proxy, which on OVH MKS enters the pod network via the konnectivity agents in
`kube-system` — the `allow-apiserver-to-api` NetworkPolicy exists for exactly
that hop. Forge is the first consumer; it authenticates to Rancher with the
credentials it already holds, so no new secret exists anywhere on its side.

## Dashboard

`dashboards/capacity.json` must stay identical to
`grafana/dashboards/capacity.json` (the local docker-compose Grafana reads the
latter); `tests/test_chart_assets.py` fails the build when they drift.
