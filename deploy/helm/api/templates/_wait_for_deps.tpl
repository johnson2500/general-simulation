{{/*
Init container that blocks until Postgres and Neo4j accept TCP connections.
Used so the API process never races cold database starts on OpenShift.
*/}}
{{- define "api.waitForDepsInit" -}}
{{- if .Values.waitFor.enabled }}
initContainers:
  - name: wait-for-deps
    image: {{ .Values.image }}
    imagePullPolicy: Always
    command:
      - python
      - -c
      - |
        import socket, sys, time
        targets = [
            ({{ .Values.postgres.host | quote }}, {{ .Values.postgres.port }}),
            ({{ .Values.neo4j.host | quote }}, {{ .Values.neo4j.port }}),
        ]
        timeout = {{ .Values.waitFor.timeoutSeconds }}
        deadline = time.time() + timeout
        for host, port in targets:
            print(f"Waiting for {host}:{port} (timeout={timeout}s)...", flush=True)
            while True:
                try:
                    with socket.create_connection((host, port), timeout=2):
                        print(f"{host}:{port} is up", flush=True)
                        break
                except OSError as exc:
                    if time.time() >= deadline:
                        print(f"Timed out waiting for {host}:{port}: {exc}", file=sys.stderr)
                        sys.exit(1)
                    time.sleep(2)
        print("Dependencies ready", flush=True)
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: ["ALL"]
    volumeMounts:
      - name: tmp
        mountPath: /tmp
{{- end }}
{{- end }}
