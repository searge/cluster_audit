{{- define "platform-capacity.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "platform-capacity.image" -}}
{{ printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}

{{/*
Selector labels for the API pods. Kept minimal and stable: a Deployment's
selector is immutable, so anything volatile here (chart version, image tag)
would break the first upgrade.
*/}}
{{- define "platform-capacity.apiSelector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/component: api
{{- end }}
