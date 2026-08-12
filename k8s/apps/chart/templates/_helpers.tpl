{{- define "apps-common.labels" -}}
app.kubernetes.io/part-of: devops-platform
app.kubernetes.io/managed-by: Helm
{{- end -}}

{{- define "apps-common.image" -}}
{{- $svc := . -}}
{{- if $svc.image -}}
{{ $.Values.registry }}/{{ $svc.name }}@{{ $svc.image }}
{{- else if $.Values.registry -}}
{{ $.Values.registry }}/{{ $svc.name }}:{{ $svc.tag | default $.Values.imageTag }}
{{- else -}}
{{ $svc.name }}:{{ $svc.tag | default $.Values.imageTag }}
{{- end -}}
{{- end -}}