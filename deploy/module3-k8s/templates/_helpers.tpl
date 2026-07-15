{{/*
Expand chart name.
*/}}
{{- define "module3-k8s.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
