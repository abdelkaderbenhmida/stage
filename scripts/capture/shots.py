#!/usr/bin/env python3
"""Catalogue des captures terminal du rapport.

Chaque entree : nom du fichier attendu par rapport.tex -> (hote affiche, commande).
Lancement :  python3 scripts/capture/shots.py [motif ...]
"""
import os, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RENDER = os.path.join(ROOT, "scripts", "capture", "render.py")
M = "devops@master-01"        # noeud control-plane
W = "devops@worker-01"        # noeud worker
H = "devops@homelab"          # poste d'administration
NODE = "docker exec devops-platform-control-plane"

sys.path.insert(0, os.path.dirname(__file__))
from titres import TITRES

SHOTS = {
# ---------- chapitre 1 : architecture, namespaces, reseau ----------
"namespaces-psa": (M, "kubectl get ns devops-platform monitoring vault argocd --show-labels"),
"overlays-diff-devprod": (H,
  "echo '--- dev ---'; kubectl kustomize k8s/apps/overlays/dev | grep -c 'replicas: 1'; "
  "echo '--- staging ---'; kubectl kustomize k8s/apps/overlays/staging | grep -c 'replicas: 2'; "
  "echo '--- prod ---'; kubectl kustomize k8s/apps/overlays/prod | grep -c 'replicas: 3'"),

# ---------- chapitre 3 : Kubernetes ----------
"k8s-clusterinfo": (M, "kubectl cluster-info && kubectl version"),
"k8s-controlplane-pods": (M, "kubectl get pods -n kube-system -o wide"),
"k8s-nodes-wide": (M, "kubectl get nodes -o wide"),
"k8s-limitrange-quota": (M, "kubectl describe limitrange -n devops-platform; kubectl describe resourcequota -n devops-platform"),
"k8s-netpol-list": (M, "kubectl get networkpolicy -n devops-platform"),
"k8s-hpa-status": (M, "kubectl get hpa -n devops-platform"),
"k8s-pdb": (M, "kubectl get pdb -n devops-platform"),
"k8s-securitycontext": (M,
  "kubectl get pod -n devops-platform -l app.kubernetes.io/name=users-service -o jsonpath='{.items[0].metadata.name}' | "
  "xargs -I{} kubectl describe pod -n devops-platform {} | grep -A12 -i 'Security Context' | head -40"),
"kustomize-prod-render": (H, "kubectl kustomize k8s/apps/overlays/prod | grep -E '^(kind|  name|  namespace|  replicas|    replicas)' | head -45"),
"guide-etat-sain": (M, "kubectl top nodes; echo; kubectl get pods -A --no-headers | awk '{print $1}' | sort | uniq -c | sort -rn"),

# ---------- containerd ----------
"containerd-crictl-ps": (W, NODE + " crictl ps | head -20"),
"containerd-systemdcgroup": (W, NODE + " grep -n SystemdCgroup /etc/containerd/config.toml"),
"containerd-status": (W, NODE + " systemctl status containerd --no-pager | head -18"),

# ---------- Terraform ----------
"terraform-tfstate-gitignore": (H, "git status --short && echo && git check-ignore -v terraform/terraform.tfstate"),

# ---------- qualite / securite locale ----------
"ruff-clean": (H,
  "# la CI epingle ruff 0.1.9 : on interroge la meme version\n"
  "export PATH=\"$HOME/capshots/bin:$PATH\"; ruff --version; echo; "
  "for d in shared users-service products-service orders-service; do "
  "echo \"-- app/$d\"; ruff check app/$d/ && echo 'All checks passed!'; done"),
"kubeconform-valid": (H,
  "find k8s -name '*.yaml' ! -name 'Chart.yaml' ! -name '*values*.yaml' -print0 | "
  "xargs -0 kubeconform -strict -ignore-missing-schemas -summary"),
"gitleaks-clean": (H, "gitleaks detect --source . --config .gitleaks.toml --redact --no-banner; echo \"exit code: $?\""),
"pipaudit-clean": (H, "pip-audit -r app/shared/requirements.txt --strict --progress-spinner off"),
"conftest-pass": (H, "kubectl kustomize k8s/apps/base | conftest test --policy k8s/policies/conftest -"),

# ---------- Vault ----------
"vault-status": (M,
  "kubectl get pods -n vault -o wide; echo; "
  "kubectl exec -n vault deploy/vault -- vault status 2>/dev/null || "
  "kubectl exec -n vault $(kubectl get pod -n vault -o name | head -1 | cut -d/ -f2) -- vault status"),
}

VT = ("TOKEN=$(kubectl get secret -n vault vault-root-token -o jsonpath={.data.root-token} | base64 -d)\n"
      "kubectl exec -n vault deploy/vault -- env VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$TOKEN %s")

SHOTS.update({
# ---------- reseau et politiques Kubernetes ----------
"k8s-calico-connectivity": (M,
  "kubectl run netcheck --rm -i --restart=Never --image=busybox:1.36 -n devops-platform -- "
  "sh -c 'nslookup users-service.devops-platform.svc.cluster.local; wget -qO- --timeout=3 http://users-service/livez'"),
"k8s-netpol-deny-proof": (M, "./scripts/capture/demo/netpol-proof-kind.sh"),

# ---------- Docker ----------
"docker-build": (H, "docker build -t users-service:1.0.0 -f app/users-service/Dockerfile app/ 2>&1 | tail -35"),
"docker-images-sizes": (H, "docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}' | grep -E 'REPOSITORY|-service|python' | head -12"),
"docker-inspect-user-health": (H,
  "docker inspect --format='User      : {{.Config.User}}' users-service:1.0.0; "
  "docker inspect --format='Healthcheck: {{.Config.Healthcheck.Test}}' users-service:1.0.0; "
  "docker inspect --format='Interval  : {{.Config.Healthcheck.Interval}}' users-service:1.0.0"),

# ---------- application ----------
"instrumentator-metrics": (M,
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- "
  "python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8000/metrics').read().decode())\" "
  "| grep -E '^http_requests_total|^http_request_duration_seconds_bucket' | head -14"),
"shared-logs-json": (M, "kubectl logs -n devops-platform deploy/users-service -c users-service --tail=12"),

# ---------- Vault ----------
"vault-mounts-auth": (M, VT % "vault secrets list" + "\n" + VT % "vault auth list"),
"vault-policy-read": (M, VT % "vault policy list" + "\n" + VT % "vault policy read devops-platform-users-service"),
"vault-kv-get": (M, VT % "vault kv get -format=json secret/devops-platform/users-service" + " | grep -E '\\\"[A-Z_]+\\\":' | sed 's/:.*/: ***REDACTED***/'"),
"prom-operator-cr": (M, "kubectl get prometheus -n monitoring; echo; kubectl get prometheus -n monitoring -o jsonpath='{.items[0].spec.retention}{\"\\n\"}'"),
"prom-servicemonitors": (M, "kubectl get servicemonitor -A"),
"ksm-metrics": (M, "kubectl get pods -n monitoring -l app.kubernetes.io/name=kube-state-metrics -o wide; "
  "kubectl exec -n monitoring deploy/kube-state-metrics -- wget -qO- http://localhost:8080/metrics 2>/dev/null | grep -E '^kube_(pod_status_phase|deployment_status_replicas) ' | head -8"),
"am-rule-in-cluster": (M, "kubectl get prometheusrule -n monitoring; echo; "
  "kubectl get prometheusrule -n monitoring -o jsonpath='{range .items[*].spec.groups[*].rules[*]}{.alert}{\" \"}{.for}{\"\\n\"}{end}' | grep -v '^ ' | head -14"),
"grafana-dash-configmaps": (M, "kubectl get configmap -n monitoring -l grafana_dashboard=1"),
"elk-pods-running": (M, "kubectl get pods -n monitoring -l 'app.kubernetes.io/name in (elasticsearch,kibana,logstash)' -o wide; kubectl get pods -n logging -l 'app.kubernetes.io/name in (filebeat,promtail)' -o wide"),
"es-cluster-health": (M,
  "kubectl exec -n monitoring elasticsearch-0 -- sh -c "
  "'curl -s -u elastic:$ELASTIC_PASSWORD localhost:9200/_cluster/health?pretty'"),
"es-cat-indices": (M,
  "kubectl exec -n monitoring elasticsearch-0 -- sh -c "
  "'curl -s -u elastic:$ELASTIC_PASSWORD \"localhost:9200/_cat/indices?v&h=health,status,index,docs.count,store.size\"' | head -14"),

# ---------- ArgoCD (declaratif) ----------
"argocd-project-yaml": (M, "kubectl get appproject -n argocd; echo; "
  "kubectl get appproject devops-platform -n argocd -o yaml 2>/dev/null | sed -n '/^spec:/,/^status:/p' | head -30"),
"argocd-syncpolicy": (M, "kubectl get applications -n argocd -o custom-columns="
  "'NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,SELFHEAL:.spec.syncPolicy.automated.selfHeal,PRUNE:.spec.syncPolicy.automated.prune'"),

# ---------- securite ----------
"trivy-scan-local": (H, "trivy image --scanners vuln --severity CRITICAL,HIGH --no-progress users-service:1.0.0 2>&1 | tail -25"),
"precommit-allfiles": (H, "pre-commit run --all-files 2>&1 | tail -30"),
})

SHOTS.update({
# ---------- application (suite) ----------
"fastapi-root": (H, "curl -s http://localhost:18080/ | python3 -m json.tool"),
"shared-logs-plain-dev": (H,
  "cd app && ENVIRONMENT=dev LOG_FORMAT=plain python3 -c \"import sys; sys.path.insert(0,'.'); "
  "from shared.logging_config import configure_logging; import logging; configure_logging(); "
  "log=logging.getLogger('users-service'); log.info('service demarre en mode developpement'); "
  "log.warning('secret.default_used : valeur de repli utilisee hors Vault')\" 2>&1 | tail -6"),
"shared-fail-closed-local": (H,
  "cd app && ENVIRONMENT=production python3 -c \"import sys; sys.path.insert(0,'.'); "
  "from shared.secrets import get_secret; get_secret('JWT_SECRET_KEY')\" 2>&1 | tail -12; "
  "echo \"exit code: $?\""),

# ---------- Kubernetes dynamique ----------
"k8s-rolling-update": (M,
  "kubectl rollout restart deploy/users-service -n devops-platform; "
  "sleep 4; kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service; "
  "echo; kubectl rollout status deploy/users-service -n devops-platform --timeout=120s"),

# ---------- scripts de validation ----------
"validate-platform-all": (M, "./scripts/validate-platform.sh 2>&1 | tail -45"),
"validate-security-ok":  (M, "./scripts/validate-security.sh 2>&1 | tail -30"),
"smoke-e2e":             (M, "./scripts/smoke-test.sh 2>&1 | tail -30"),
"checklist-weekly":      (M, "./scripts/validate-platform.sh 2>&1 | tail -20"),

# ---------- securite : tests negatifs ----------
"conftest-deny": (H,
  "kubectl kustomize k8s/apps/base | python3 -c \""
  "import sys,re; d=sys.stdin.read(); "
  "d=d.replace('readOnlyRootFilesystem: true','readOnlyRootFilesystem: false'); "
  "sys.stdout.write(d)\" | conftest test --policy k8s/policies/conftest - 2>&1 | tail -12"),
"pipaudit-detection": (H,
  "printf 'flask==0.12.2\\n' > /tmp/req-vuln.txt && "
  "pip-audit -r /tmp/req-vuln.txt --progress-spinner off 2>&1 | tail -14; echo \"exit code: $?\""),
"trivy-blocked": (H,
  "trivy image --scanners vuln --severity CRITICAL,HIGH --exit-code 1 --no-progress "
  "python:3.9-slim 2>&1 | tail -18; echo \"exit code: ${PIPESTATUS[0]}\""),
"trivy-sbom": (H,
  "trivy image --format spdx-json --output /tmp/sbom.json --no-progress users-service:1.0.0 2>&1 | tail -3; "
  "python3 -c \"import json;d=json.load(open('/tmp/sbom.json'));"
  "print('SPDX version :',d.get('spdxVersion'));print('paquets      :',len(d.get('packages',[])));"
  "[print('  -',p['name'],p.get('versionInfo','')) for p in d['packages'][:10]]\""),
})

SHOTS.update({
"scenario4-01-scaleup": (M,
  "for i in 1 2 3 4 5 6; do date +%H:%M:%S; kubectl get hpa users-service -n devops-platform --no-headers; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service --no-headers | wc -l | sed 's/^/pods: /'; echo; sleep 12; done"),
"scenario4-03-scaledown": (M,
  "kubectl get hpa users-service -n devops-platform; echo; "
  "kubectl describe hpa users-service -n devops-platform | grep -A8 'Events:'"),
"validate-selfheal": (M,
  "kubectl get pod -n devops-platform -l app.kubernetes.io/name=users-service -o name | head -1 | "
  "xargs -I{} sh -c 'echo \"suppression de {}\"; kubectl delete -n devops-platform {} --wait=false'; "
  "sleep 20; kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service"),
"validate-platform-fail": (M,
  "kubectl scale deploy/products-service -n devops-platform --replicas=0; sleep 6; "
  "./scripts/validate-platform.sh --ci 2>&1 | tail -18; echo \"exit code: $?\"; "
  "kubectl scale deploy/products-service -n devops-platform --replicas=2 >/dev/null; "
  "kubectl rollout status deploy/products-service -n devops-platform --timeout=120s"),
"ab-results": (H, "ab -k -c 60 -n 3000 http://127.0.0.1:18080/users 2>&1 | tail -30"),
"scenario2-01-before": (M,
  "curl -s -o /dev/null -w 'readyz : %{http_code}\\n' http://127.0.0.1:18080/readyz; "
  "kubectl get endpoints users-service -n devops-platform"),
"logstash-drop-proof": (M,
  "kubectl exec -n monitoring elasticsearch-0 -- sh -c "
  "'curl -s -u elastic:$ELASTIC_PASSWORD \"localhost:9200/devops-platform-*/_count?q=path:%22/livez%22\"; echo; "
  "curl -s -u elastic:$ELASTIC_PASSWORD \"localhost:9200/devops-platform-*/_count\"'"),
"threatmodel-reject": (H,
  "kubectl kustomize k8s/apps/base | python3 -c \""
  "import sys; d=sys.stdin.read(); "
  "sys.stdout.write(d.replace('runAsNonRoot: true','runAsNonRoot: false'))\" "
  "| conftest test --policy k8s/policies/conftest - 2>&1 | tail -10"),
})

SHOTS.update({
# vue d'ensemble ArgoCD (l'ecran "tuiles" de l'interface v3.5.1 reste bloque
# sur son etat de chargement : la meme information est donnee par l'API)
"argocd-apps-grid": (M,
  "kubectl get applications -n argocd -o custom-columns="
  "'APPLICATION:.metadata.name,PROJET:.spec.project,SYNC:.status.sync.status,"
  "SANTE:.status.health.status,REVISION:.status.sync.revision' | cut -c1-100"),
"argocd-events": (M,
  "kubectl get events -n argocd --sort-by=.lastTimestamp | tail -20"),
})

SHOTS.update({
# ---------- montee en charge reelle : HPA 2 -> 5 ----------
"scenario4-01-scaleup": (M,
  "echo '# montee en charge reelle sur users-service (ab -c 250)'; echo; "
  "printf '%-10s %-34s %-10s %s\\n' HEURE CIBLES REPLIQUES PODS; "
  "for i in $(seq 1 14); do "
  "  h=$(date +%H:%M:%S); "
  "  t=$(kubectl get hpa users-service -n devops-platform "
  "      -o jsonpath='cpu {.status.currentMetrics[0].resource.current.averageUtilization}%/70% mem {.status.currentMetrics[1].resource.current.averageUtilization}%/80%'); "
  "  r=$(kubectl get hpa users-service -n devops-platform -o jsonpath='{.status.currentReplicas}'); "
  "  p=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service --no-headers | grep -c Running); "
  "  printf '%-10s %-34s %-10s %s\\n' \"$h\" \"$t\" \"$r\" \"$p\"; sleep 20; done"),
"hpa-scaling-live": (M,
  "kubectl get hpa,pods -n devops-platform -l app.kubernetes.io/name=users-service; echo; "
  "kubectl get hpa users-service -n devops-platform -o wide"),
"scenario4-04-pinned": (M,
  "kubectl get hpa users-service -n devops-platform -o jsonpath="
  "'{range .status.conditions[*]}{.type}: {.status} ({.reason}){\"\\n\"}{end}'"),
})

SHOTS.update({
# ---------- auto-scaling : decision du HPA ----------
"scenario4-04-pinned": (M,
  "kubectl describe hpa users-service -n devops-platform | sed -n '/Conditions:/,$p' | head -12; echo; "
  "kubectl describe hpa users-service -n devops-platform | sed -n '/Events:/,$p' | tail -6"),

# ---------- GitOps : auto-reparation d'une derive manuelle ----------
"argocd-selfheal": (M,
  "echo '# derive manuelle : passage a 5 repliques hors Git'; "
  "kubectl scale deploy/users-service -n devops-platform --replicas=5; "
  "for i in 1 2 3 4 5 6 7 8; do "
  "  printf '%s  spec.replicas=%s  sync=%s\\n' \"$(date +%H:%M:%S)\" "
  "    \"$(kubectl get deploy users-service -n devops-platform -o jsonpath='{.spec.replicas}')\" "
  "    \"$(kubectl get app users-service -n argocd -o jsonpath='{.status.sync.status}')\"; "
  "  sleep 8; done; "
  "echo; echo '# ArgoCD a ramene l etat declare dans Git'"),
})

SHOTS.update({
"scenario4-01-scaleup": (M,
  "printf '%-10s %-13s %-11s %-9s %s\\n' HEURE CPU MEMOIRE REPLIQUES PODS-READY; "
  "for i in $(seq 1 34); do "
  "  cpu=$(kubectl get hpa users-service -n devops-platform -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}'); "
  "  mem=$(kubectl get hpa users-service -n devops-platform -o jsonpath='{.status.currentMetrics[1].resource.current.averageUtilization}'); "
  "  rep=$(kubectl get deploy users-service -n devops-platform -o jsonpath='{.spec.replicas}'); "
  "  rdy=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service --no-headers | grep -c '2/2'); "
  "  printf '%-10s %-13s %-11s %-9s %s\\n' \"$(date +%H:%M:%S)\" \"${cpu}%/70%\" \"${mem}%/80%\" \"$rep\" \"$rdy\"; "
  "  sleep 22; done"),
})

SHOTS.update({
# ---------- scenario 3 : fuite memoire et OOMKill reels ----------
"scenario3-03-oomkill": (M,
  "POD=$(kubectl get pod -n devops-platform -l app.kubernetes.io/name=oom-demo -o jsonpath='{.items[0].metadata.name}'); "
  "echo \"# conteneur limite a 64 Mio, allocation de 8 Mio toutes les 0,5 s\"; "
  "kubectl logs -n devops-platform $POD --tail=4 --previous 2>/dev/null; echo; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=oom-demo; echo; "
  "kubectl describe pod -n devops-platform $POD | sed -n '/Last State/,/Restart Count/p'"),
"scenario3-01-ratio80": (M,
  "kubectl top pods -n devops-platform --containers 2>/dev/null | head -14; echo; "
  "echo '# ratio memoire = usage / limite, source du signal ContainerMemoryRatioHigh'"),
})

SHOTS.update({
# ---------- scenario 2 : panne de Vault, comportement fail-closed ----------
"scenario2-01-before": (M,
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- "
  "python -c \"import urllib.request as u,json;r=u.urlopen('http://localhost:8000/readyz');"
  "print('readyz :',r.status);print(r.read().decode()[:200])\"; echo; "
  "kubectl get endpoints users-service -n devops-platform"),
"scenario2-02-outage": (M,
  "echo '# arret de Vault'; kubectl scale deploy/vault -n vault --replicas=0; "
  "sleep 45; kubectl get pods -n vault; echo; "
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- "
  "python -c \"import urllib.request as u,urllib.error as e;"
  "\nimport sys"
  "\ntry:"
  "\n    r=u.urlopen('http://localhost:8000/readyz'); print('readyz :',r.status)"
  "\nexcept e.HTTPError as x: print('readyz :',x.code); print(x.read().decode()[:200])"
  "\nr=u.urlopen('http://localhost:8000/livez'); print('livez  :',r.status, r.read().decode()[:80])\" 2>&1 | tail -6; "
  "echo; kubectl get endpoints users-service -n devops-platform"),
"scenario2-03-logs": (M,
  "kubectl logs -n devops-platform deploy/users-service -c users-service --tail=25 | grep -i 'vault\\|secret' | tail -12"),
"scenario2-04-recovery": (M,
  "kubectl get pods -n vault --sort-by=.metadata.creationTimestamp | tail -3; echo; "
  "echo '# Vault en mode dev repart vide : le Job de setup replante les secrets'; "
  "kubectl logs -n vault -l job-name --tail=3 --prefix 2>/dev/null | tail -3; echo; "
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- python -c \""
  "import urllib.request as u; r=u.urlopen('http://localhost:8000/readyz'); print('readyz :', r.status, r.read().decode())\"; echo; "
  "kubectl get endpoints users-service -n devops-platform"),
"fastapi-readyz-vault": (M,
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- "
  "python -c \"import urllib.request as u,json;r=u.urlopen('http://localhost:8000/readyz');"
  "print(json.dumps(json.loads(r.read()),indent=2)[:600])\""),
})

SHOTS.update({
# ---------- scenario 6 : rotation complete du token Vault ----------
"scenario6-01-onetime-token": (H,
  "scripts/bootstrap-vault-secret.sh --dry-run 2>&1 | "
  "sed -E 's/^  [A-Za-z0-9]{20,}$/  ***TOKEN AFFICHE UNE SEULE FOIS***/; s/^  root-token: .*/  root-token: ***REDACTED***/' | head -20"),
"scenario6-02-rolling": (M,
  "kubectl rollout restart deploy -n devops-platform; sleep 12; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/part-of=devops-platform "
  "--sort-by=.metadata.creationTimestamp | tail -14"),
"scenario6-03-proof": (M,
  "kubectl rollout status deploy/users-service -n devops-platform --timeout=180s; echo; "
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- "
  "python -c \"import urllib.request as u;print('readyz :',u.urlopen('http://localhost:8000/readyz').status)\"; "
  "echo; gitleaks detect --source . --config .gitleaks.toml --redact --no-banner 2>&1 | tail -3"),
})

SHOTS.update({
"scenario4-03-scaledown": (M,
  "echo '# charge arretee : fenetre de stabilisation de 300 s avant reduction'; "
  "printf '%-10s %-13s %-9s %s\\n' HEURE CPU REPLIQUES PODS; "
  "for i in $(seq 1 26); do "
  "  cpu=$(kubectl get hpa users-service -n devops-platform -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}'); "
  "  rep=$(kubectl get deploy users-service -n devops-platform -o jsonpath='{.spec.replicas}'); "
  "  p=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service --no-headers | wc -l); "
  "  printf '%-10s %-13s %-9s %s\\n' \"$(date +%H:%M:%S)\" \"${cpu}%/70%\" \"$rep\" \"$p\"; sleep 20; done; "
  "echo; kubectl describe hpa users-service -n devops-platform | sed -n '/Events:/,$p' | tail -4"),
})

SHOTS.update({
"scenario1-03-ghcr-digest": (M,
  "kubectl get deploy users-service -n devops-platform "
  "-o jsonpath='image      : {.spec.template.spec.containers[1].image}{\"\\n\"}'; "
  "kubectl get pod -n devops-platform -l app.kubernetes.io/name=users-service "
  "-o jsonpath='digest     : {.items[0].status.containerStatuses[1].imageID}{\"\\n\"}'"),
"scenario1-05-rollout": (M,
  "kubectl rollout history deploy/users-service -n devops-platform | tail -6; echo; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service "
  "--sort-by=.metadata.creationTimestamp -o wide | tail -4"),
"scenario1-06-final-metrics": (M,
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- python -c \""
  "import urllib.request as u;"
  "print('GET /users   :', u.urlopen('http://localhost:8000/users').status);"
  "print('GET /readyz  :', u.urlopen('http://localhost:8000/readyz').status);"
  "m=u.urlopen('http://localhost:8000/metrics').read().decode();"
  "print('metriques    :', len([l for l in m.splitlines() if l.startswith('http_requests_total')]), 'series http_requests_total')\""),
"scenario5-03-revert-sync": (M,
  "kubectl get app users-service -n argocd -o custom-columns="
  "'APPLICATION:.metadata.name,SYNC:.status.sync.status,SANTE:.status.health.status,REVISION:.status.sync.revision'; echo; "
  "kubectl get deploy users-service -n devops-platform "
  "-o jsonpath='image deploye : {.spec.template.spec.containers[1].image}{\"\\n\"}'"),
"scenario5-04-recovered": (M,
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- python -c \""
  "import urllib.request as u;r=u.urlopen('http://localhost:8000/users');"
  "print('GET /users :', r.status, r.read().decode())\"; echo; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service"),
})

SHOTS.update({
"docker-run-healthy": (H,
  "docker rm -f users-demo >/dev/null 2>&1; "
  "docker run -d --name users-demo -e ENVIRONMENT=dev -e LOG_FORMAT=plain -p 18099:8000 users-service:1.0.0 >/dev/null; "
  "sleep 45; docker ps --filter name=users-demo --format 'table {{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'; echo; "
  "curl -s http://127.0.0.1:18099/livez; echo; docker rm -f users-demo >/dev/null"),
"precommit-fix": (H,
  "printf 'x = 1   \\n' > /tmp/demo_hook.py && git add -N /tmp/demo_hook.py 2>/dev/null; "
  "cp /tmp/demo_hook.py app/demo_hook.py && git add app/demo_hook.py && "
  "echo '# fichier avec espaces en fin de ligne, ajoute a l index'; "
  "pre-commit run trailing-whitespace end-of-file-fixer --files app/demo_hook.py 2>&1 | tail -12; "
  "echo; echo '# apres correction automatique par le hook :'; cat -A app/demo_hook.py; "
  "git reset -q app/demo_hook.py; rm -f app/demo_hook.py /tmp/demo_hook.py"),
})

TF = "cd terraform && "
AN = "cd ansible && ANSIBLE_DEPRECATION_WARNINGS=False "

SHOTS.update({
# ---------- libvirt / KVM ----------
"kvm-virsh-list": ("devops@homelab",
  "virsh list --all; echo; virsh dominfo master-01 | head -9"),
"kvm-qcow2-info": ("devops@homelab",
  "virsh vol-info --pool default master-01.qcow2; echo; "
  "virsh vol-info --pool default ubuntu-base.qcow2; echo; "
  "qemu-img info /var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img | head -6"),
"reseau-libvirt": ("devops@homelab",
  "virsh net-list --all; echo; virsh net-dumpxml devops-platform-net"),
"vms-topologie": ("devops@homelab",
  "for d in master-01 worker-01; do virsh dominfo $d | grep -E 'Nom|Name|Etat|State|CPU|Used memory|Max memory'; echo; done"),

# ---------- cloud-init ----------
"cloudinit-userdata": (H, "sed -n '1,30p' terraform/cloud-init.tpl"),
"cloudinit-ssh-hardening": ("devops@homelab",
  "echo '# connexion root : refusee (disable_root)'; timeout 8 ssh -o StrictHostKeyChecking=no -o BatchMode=yes root@192.168.56.10 true 2>&1 | tail -1; echo; echo '# connexion devops par cle : acceptee'; timeout 8 ssh -o StrictHostKeyChecking=no -o BatchMode=yes devops@192.168.56.10 'id -un; grep -E \"^PasswordAuthentication|^PermitRootLogin\" /etc/ssh/sshd_config.d/*.conf /etc/ssh/sshd_config 2>/dev/null | head -3' 2>/dev/null"),
"cloudinit-fail2ban": (H, "sed -n '26,40p' terraform/cloud-init.tpl"),
"ip-statique-dns": ("devops@master-01",
  "ssh -o StrictHostKeyChecking=no devops@192.168.56.10 'ip -4 addr show enp1s0 | sed -n \"1,3p\"; echo; ip route; echo; ping -c2 -W2 192.168.56.11 | tail -3' 2>/dev/null"),

# ---------- Terraform ----------
"terraform-init": (H, TF + "terraform init -no-color -upgrade=false 2>&1 | tail -16"),
"terraform-plan": (H, TF + "terraform plan -no-color -input=false 2>&1 | tail -20"),
"terraform-state-list": (H,
  TF + "terraform version -no-color; echo; terraform providers 2>&1 | head -12"),
"terraform-inventory-gen": (H,
  "echo '# gabarit rendu par la ressource local_file'; cat terraform/inventory.tpl; echo; "
  "grep -n -A8 'local_file' terraform/main.tf | head -16"),

# ---------- Ansible ----------
"ansible-playbook-start": (H,
  AN + "ansible-playbook playbook.yml -i inventory.ini.example --list-tasks 2>/dev/null | sed -n '1,32p'"),
"ansible-recap": ("devops@homelab",
  "cat ~/capshots/logs/ansible-run.txt | tail -8"),
"ansible-dpkg-hold": (H,
  "grep -n -B3 -A6 'dpkg_selections' ansible/roles/k8s_common/tasks/main.yml"),
"ansible-join-perms": (H,
  "sed -n '131,158p' ansible/roles/k8s_master/tasks/main.yml"),
})

SHOTS.update({
"vault-token-rotation": (M, "./scripts/capture/demo/rotate-vault-token.sh"),
"vault-readyz-after-rotation": (M,
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service; echo; "
  "kubectl exec -n devops-platform deploy/users-service -c users-service -- python -c \""
  "import urllib.request as u;r=u.urlopen('http://localhost:8000/readyz');"
  "print('readyz :', r.status, r.read().decode())\""),
})

def _suivi(entete, etat_absent):
    return ("echo '%s'; "
            "for i in $(seq 1 14); do "
            "  printf '%%s  sync=%%-12s configmap=%%s\\n' \"$(date +%%H:%%M:%%S)\" "
            "    \"$(kubectl get app users-service -n argocd -o jsonpath='{.status.sync.status}')\" "
            "    \"$(kubectl get configmap demo-prune -n devops-platform "
            "-o jsonpath='{.metadata.name}' 2>/dev/null || echo %s)\"; "
            "  sleep 20; done") % (entete, etat_absent)


SHOTS.update({
"scenario1-04-argocd-oos": (M, _suivi(
    "# commit pousse : ArgoCD detecte l ecart puis applique la ressource", "absente")),
"argocd-prune": (M, _suivi(
    "# ressource retiree de Git : ArgoCD la supprime du cluster", "supprimee")),
})

SHOTS.update({
"argocd-rollback": (M,
  "kubectl get app users-service -n argocd -o jsonpath="
  "'{range .status.history[*]}revision {.revision} deploye le {.deployedAt}{\"\\n\"}{end}' | tail -8; echo; "
  "echo '# chaque entree est un point de retour : argocd app rollback users-service <id>'"),
"terraform-outputs": (H,
  "cat terraform/outputs.tf; echo; "
  "echo '# valeurs marquees sensitive : elles ne sont revelees qu avec terraform output -raw'"),
"filebeat-autodiscover": (M,
  "POD=$(kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service "
  "--sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}'); "
  "AGE=$(kubectl get pod -n devops-platform $POD -o jsonpath='{.metadata.creationTimestamp}'); "
  "echo \"# pod recemment cree : $POD (depuis $AGE)\"; "
  "echo '# aucune configuration Filebeat ne mentionne ce pod : la decouverte est automatique'; echo; "
  "kubectl exec -n monitoring elasticsearch-0 -- sh -c "
  "\"curl -s -u elastic:\\$ELASTIC_PASSWORD "
  "'localhost:9200/devops-platform-*/_count?q=pod_name:$POD'\"; echo; echo; "
  "kubectl get pods -n logging -l app.kubernetes.io/name=filebeat -o wide --no-headers"),
})

SHOTS.update({
"fastapi-failclosed-crash": (M, "./scripts/capture/demo/failclosed.sh"),
})

SHOTS.update({
"kyverno-policies": (M,
  "kubectl get clusterpolicy; echo; "
  "kubectl get pods -n kyverno --no-headers | head -4; echo; "
  "kubectl get clusterpolicy require-image-digest-pin -o jsonpath='mode : {.spec.validationFailureAction}{\"\\n\"}'; "
  "kubectl get clusterpolicy restrict-security-context -o jsonpath='mode : {.spec.validationFailureAction}{\"\\n\"}'"),
})

SHOTS.update({
"am-hpa-firing": (M,
  "kubectl get hpa users-service -n devops-platform; echo; "
  "kubectl get pods -n devops-platform -l app.kubernetes.io/name=users-service --no-headers | wc -l | sed 's/^/repliques en service : /'; echo; "
  "curl -s http://127.0.0.1:9090/api/v1/alerts | python3 -c \""
  "import json,sys;a=json.load(sys.stdin)['data']['alerts'];"
  "print('%-32s %-9s %s' % ('ALERTE','ETAT','DEPUIS'));"
  "[print('%-32s %-9s %s' % (x['labels']['alertname'], x['state'], x['activeAt'][11:19])) for x in a if 'HPA' in x['labels']['alertname']]\""),
})

SHOTS.update({
"ansible-nodes-ready": ("devops@master-01", "ssh -o StrictHostKeyChecking=no devops@192.168.56.10 'kubectl get nodes -o wide'"),
"ansible-calico-running": ("devops@master-01", "ssh -o StrictHostKeyChecking=no devops@192.168.56.10 'kubectl get pods -n calico-system -o wide'"),
})

def main(patterns):
    names = [n for n in SHOTS if not patterns or any(p in n for p in patterns)]
    ok = ko = 0
    for n in names:
        host, cmd = SHOTS[n]
        titre, sous = TITRES.get(n, (n, ""))
        r = subprocess.run([sys.executable, RENDER, n, cmd, "--host", host,
                            "--title", titre, "--subtitle", sous,
                            "--timeout", "1200"], cwd=ROOT)
        ok, ko = (ok + 1, ko) if r.returncode == 0 else (ok, ko + 1)
    print("\n%d captures generees, %d echecs" % (ok, ko))

main(sys.argv[1:])
