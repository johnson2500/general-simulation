{{- define "ingestion.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: general-sim-ingestion
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: general-sim
app.kubernetes.io/component: ingestion
{{- end }}

{{- define "ingestion.selectorLabels" -}}
app.kubernetes.io/name: general-sim-ingestion
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ingestion.postgresDSN" -}}
postgresql://{{ .Values.postgres.user }}:{{ required "postgres.password is required" .Values.postgres.password }}@{{ .Values.postgres.host }}:{{ .Values.postgres.port }}/{{ .Values.postgres.database }}
{{- end }}

{{- define "ingestion.neo4jURI" -}}
bolt://{{ .Values.neo4j.host }}:{{ .Values.neo4j.port }}
{{- end }}
