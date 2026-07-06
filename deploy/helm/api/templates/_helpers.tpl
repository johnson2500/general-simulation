{{- define "api.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: general-sim-api
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: general-sim
app.kubernetes.io/component: api
{{- end }}

{{- define "api.selectorLabels" -}}
app.kubernetes.io/name: general-sim-api
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Construct the Postgres DSN from individual values.
*/}}
{{- define "api.postgresDSN" -}}
postgresql://{{ .Values.postgres.user }}:{{ required "postgres.password is required" .Values.postgres.password }}@{{ .Values.postgres.host }}:{{ .Values.postgres.port }}/{{ .Values.postgres.database }}
{{- end }}

{{/*
Construct the Neo4j Bolt URI from individual values.
*/}}
{{- define "api.neo4jURI" -}}
bolt://{{ .Values.neo4j.host }}:{{ .Values.neo4j.port }}
{{- end }}
