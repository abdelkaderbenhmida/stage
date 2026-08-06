# DevOps Central Platform — Test Commands

Complete sequence to test every tool in the platform end-to-end.

> **Prerequisites:** Docker, kubectl, terraform, ansible, helm, jq, python3, pip3, ruff, yamllint, gitleaks.
>
> **Context:** Run from `/home/gadour/Desktop/stage`.

---

## 0. Quick pre-flight (all tools before anything else)

```bash
cd /home/gadour/Desktop/stage

# Check which tools are installed
for t in terraform ansible docker kubectl helm jq yamllint ruff python3 pip3 trivy gitleaks pre-commit k6 vault kubeconform conftest yq; do
  command -v "$t" >/dev/null 2>&1 && echo "  ✅ $t ($("$t" --version 2>&1 | head -1 | tr -d '\n'))" || echo "  ❌ $t NOT INSTALLED"
done
```

```bash
# Verify user is in required groups (docker, libvirt)
groups
```

```bash
# Verify SSH keys exist
ls -la ~/.ssh/id_ed25519 ~/.ssh/id_ed25519.pub
```

```bash
# Verify libvirt daemon
virsh list --all
```

```bash
# Verify kubeconfig
kubectl cluster-info
```

```bash
# Verify cloud image for Terraform
ls -lh /var/lib/libvirt/images/ubuntu-*.img
```

---

## Phase 1 — Static analysis (no cluster needed)

### ruff — Python lint

```bash
ruff check app/
```

### yamllint — YAML validation

```bash
yamllint -s k8s/ ansible/
```

### gitleaks — secret scan

```bash
gitleaks detect --source . --config .gitleaks.toml --no-banner --redact
```

### pre-commit — all hooks locally

```bash
pre-commit run --all-files
```

### pip-audit — dependency audit

```bash
pip-audit -r app/shared/requirements.txt -r app/users-service/requirements.txt -r app/products-service/requirements.txt -r app/orders-service/requirements.txt --strict
```

---

## Phase 2 — Terraform (offline — no VMs needed for init/validate/fmt/plan)

> ⚠️ **`terraform apply`** requires libvirt daemon running + Ubuntu cloud image at `/var/lib/libvirt/images/`. See Phase 3.

```bash
cd /home/gadour/Desktop/stage/terraform
```

```bash
terraform init
```

```bash
terraform validate
```

```bash
terraform fmt -check -recursive .
```

```bash
terraform plan -out=plan.out
```

```bash
# Show plan summary (resources to add/change/destroy)
terraform show plan.out 2>/dev/null || echo "Plan saved to plan.out"
```

```bash
cd /home/gadour/Desktop/stage
```

---

## Phase 3 — Terraform apply (provision KVM VMs — needs libvirt + KVM)

> **Prerequisites:**
> - `libvirtd` active: `systemctl status libvirtd`
> - KVM available: `ls /dev/kvm`
> - Your user in `libvirt` + `kvm` groups
> - Ubuntu cloud image at `/var/lib/libvirt/images/ubuntu-22.04-server-cloudimg-amd64.img`

```bash
cd /home/gadour/Desktop/stage/terraform
```

```bash
# Copy terraform.tfvars from example (if not already done)
cp terraform.tfvars.example terraform.tfvars
```

```bash
terraform init
```

```bash
terraform plan -out=plan.out
```

```bash
terraform apply plan.out
```

```bash
# Check VMs are running
virsh list --all
```

```bash
cd /home/gadour/Desktop/stage
```

```bash
# Generate Ansible inventory from Terraform state
./scripts/generate-inventory.sh
```

---

## Phase 4 — Ansible (config VMs + bootstrap K8s)

### Syntax checks (doit even without VMs running)

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

```bash
ansible-playbook ansible/playbook.yml --syntax-check
```

```bash
ansible-playbook ansible/playbook.yml -i ansible/inventory.ini --list-hosts
```

### Dry-run all plays (recommended before real run)

```bash
ansible-playbook ansible/playbook.yml --check --diff
```

### Apply all roles (needs VMs running from Phase 3)

```bash
ansible-playbook ansible/playbook.yml
```

### Apply only one role at a time for troubleshooting

```bash
ansible-playbook ansible/playbook.yml --tags docker
```

```bash
ansible-playbook ansible/playbook.yml --tags k8s
```

```bash
ansible-playbook ansible/playbook.yml --tags master
```

```bash
ansible-playbook ansible/playbook.yml --tags worker
```

### Reset cluster (destructive — opt-in only)

```bash
ansible-playbook ansible/playbook.yml --tags reset -e reset_confirmed=true
```

---

## Phase 5 — Docker (build images + runtime smoke)

```bash
cd /home/gadour/Desktop/stage
```

```bash
docker build -t users-service:test -f app/users-service/Dockerfile app/
```

```bash
docker build -t products-service:test -f app/products-service/Dockerfile app/
```

```bash
docker build -t orders-service:test -f app/orders-service/Dockerfile app/
```

### Smoke test each container (dev mode)

```bash
docker run --rm -d --name u -e ENVIRONMENT=dev -p 18000:8000 users-service:test && sleep 2
curl -s http://127.0.0.1:18000/livez && echo
curl -s http://127.0.0.1:18000/users && echo
docker stop u
```

```bash
docker run --rm -d --name p -e ENVIRONMENT=dev -p 18001:8000 products-service:test && sleep 2
curl -s http://127.0.0.1:18001/products && echo
docker stop p
```

```bash
docker run --rm -d --name o -e ENVIRONMENT=dev -p 18002:8000 orders-service:test && sleep 2
curl -s http://127.0.0.1:18002/orders && echo
docker stop o
```

### List all images

```bash
docker images | grep -E 'users|products|orders'
```

---

## Phase 6 — Kubernetes (cluster verification)

```bash
kubectl config current-context
```

```bash
kubectl cluster-info
```

```bash
kubectl get nodes -o wide
```

```bash
kubectl get namespaces
```

```bash
kubectl get pods -A
```

```bash
kubectl get pods -n devops-platform
```

```bash
kubectl get pods -n monitoring
```

```bash
kubectl get pods -n argocd
```

```bash
kubectl get pods -n vault
```

```bash
kubectl top nodes
```

```bash
kubectl top pods -n devops-platform
```

### Deploy platform manifests

```bash
kubectl apply -f k8s/apps/base/namespace.yaml
```

```bash
kubectl apply -f k8s/vault/manifests.yaml
```

```bash
kubectl apply -k k8s/apps/
```

```bash
kubectl apply -k k8s/monitoring/
```

```bash
kubectl apply -k k8s/argocd/install/
```

### Watch deployments rollout

```bash
kubectl -n devops-platform rollout status deployment/users-service
```

```bash
kubectl -n devops-platform rollout status deployment/products-service
```

```bash
kubectl -n devops-platform rollout status deployment/orders-service
```

### HPA verification

```bash
kubectl get hpa -n devops-platform
```

### Network policies

```bash
kubectl get networkpolicies -n devops-platform
```

### Pod Disruption Budgets

```bash
kubectl get pdb -n devops-platform
```

### RBAC

```bash
kubectl get roles,rolebindings -n devops-platform
```

### Pod logs (useful for debug)

```bash
kubectl logs -n devops-platform deploy/users-service --tail=20
```

```bash
kubectl logs -n devops-platform deploy/products-service --tail=20
```

```bash
kubectl logs -n devops-platform deploy/orders-service --tail=20
```

---

## Phase 7 — Vault (Secret management)

```bash
Vault_POD=$(kubectl get pods -n vault -l app.kubernetes.io/name=vault -o jsonpath='{.items[0].metadata.name}')
```

```bash
kubectl exec -n vault "$Vault_POD" -- vault status
```

```bash
kubectl exec -n vault "$Vault_POD" -- vault secrets list
```

```bash
kubectl exec -n vault "$Vault_POD" -- vault policy list
```

### Bootstrap vault-root-token Secret

```bash
./scripts/bootstrap-vault-secret.sh
```

### Read token from Kubernetes Secret

```bash
TOKEN=$(kubectl get secret vault-root-token -n devops-platform -o jsonpath='{.data.root-token}' | base64 -d)
echo "Token retrieved (hidden)"
```

### Verify token works on Vault

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault token lookup -format=json" 2>/dev/null || echo "Check VAULT_TOKEN env"
```

### Populate Vault KV secrets for services

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv put secret/devops-platform/users-service DATABASE_URL='sqlite:///:memory:' JWT_SECRET_KEY='dev-secret'" 2>&1
```

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv put secret/devops-platform/products-service DATABASE_URL='sqlite:///:memory:' API_KEY='dev-api-key'" 2>&1
```

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv put secret/devops-platform/orders-service DATABASE_URL='sqlite:///:memory:' PAYMENT_GATEWAY_KEY='dev-pg-key'" 2>&1
```

### Read Vault KV secrets (verify write)

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv get -mount=secret devops-platform/users-service"
```

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv get -mount=secret devops-platform/products-service"
```

```bash
kubectl exec -n vault "$Vault_POD" -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault kv get -mount=secret devops-platform/orders-service"
```

---

## Phase 8 — Prometheus

```bash
kubectl port-forward -n monitoring deploy/prometheus 9090:9090 &
sleep 2
```

```bash
curl -s http://127.0.0.1:9090/-/healthy
```

```bash
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool | head -30
```

```bash
curl -s http://127.0.0.1:9090/api/v1/query?query=up | python3 -c "
import sys,json
data=json.load(sys.stdin)
for r in data['data']['result']:
    labels=r['metric']
    print(f\"  job={labels.get('job','?')}, instance={labels.get('instance','?')} => up={r['value'][1]}\")
"
```

```bash
# Access via browser
echo "Prometheus: http://localhost:9090"
```

```bash
kill %1 2>/dev/null
```

---

## Phase 9 — Grafana

```bash
kubectl port-forward -n monitoring deploy/grafana 3000:3000 &
sleep 2
```

```bash
curl -s http://127.0.0.1:3000/api/health
```

```bash
curl -s http://127.0.0.1:3000/api/datasources
```

```bash
# Get admin password
kubectl get secret grafana-admin -n monitoring -o jsonpath='{.data.admin-password}' | base64 -d && echo
```

```bash
echo "Grafana → http://localhost:3000 (admin / password above)"
echo "Dashboards → http://localhost:3000/dashboards"
```

```bash
kill %1 2>/dev/null
```

---

## Phase 10 — Alertmanager

```bash
kubectl port-forward -n monitoring deploy/alertmanager 9093:9093 &
sleep 1
curl -s http://127.0.0.1:9093/api/v2/status
```

```bash
kill %1
```

### Check SLO rules

```bash
kubectl get cm -n monitoring alertmanager-config -o yaml | kubectl neat
```

---

## Phase 11 — ELK (Elasticsearch + Filebeat + Kibana)

### Elasticsearch

```bash
kubectl port-forward -n monitoring elasticsearch-0 9200:9200 &
sleep 2
curl -s http://127.0.0.1:9200/_cluster/health | python3 -m json.tool
```

```bash
curl -s http://127.0.0.1:9200/_cat/indices?v
```

```bash
kill %1
```

### Kibana

```bash
kubectl port-forward -n monitoring deploy/kibana 5601:5601 &
sleep 2
curl -s http://127.0.0.1:5601/api/status
```

```bash
curl -s http://127.0.0.1:5601/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"Kibana: {d['status']['overall']['state']}\")"
```

```bash
echo "Kibana → http://localhost:5601"
```

```bash
kill %1
```

---

## Phase 12 — ArgoCD

```bash
kubectl port-forward -n argocd deploy/argocd-server 8080:8080 &
sleep 2
curl -s http://127.0.0.1:8080/api/version
```

```bash
# Get admin password
kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' | base64 -d && echo
```

```bash
# List ArgoCD applications
kubectl get applications -A
```

```bash
# Check sync status
kubectl get applications -A --no-headers & while read ns name; do
  SYNC=$(kubectl get application -n "$ns" "$name" -o jsonpath='{.status.sync.status}')
  HEALTH=$(kubectl get application -n "$ns" "$name" -o jsonpath='{.status.health.status}')
  echo "  ${name}: sync=${SYNC} health=${HEALTH}"
done
```

```bash
echo "ArgoCD UI → http://localhost:8080"
```

```bash
kill %1
```

---

## Phase 13 — Microservices (FastAPI endpoints)

### users-service

```bash
kubectl port-forward -n devops-platform deploy/users-service 18000:8000 &
sleep 1

curl -s http://127.0.0.1:18000/livez && echo
curl -s http://127.0.0.1:18000/ && echo
curl -s http://127.0.0.1:18000/users && echo
curl -s http://127.0.0.1:18000/readyz && echo
curl -s http://127.0.0.1:18000/metrics | head -5

kill %1
```

### products-service

```bash
kubectl port-forward -n devops-platform deploy/products-service 18001:8000 &
sleep 1

curl -s http://127.0.0.1:18001/livez && echo
curl -s http://127.0.0.1:18001/products && echo

kill %1
```

### orders-service

```bash
kubectl port-forward -n devops-platform deploy/orders-service 18002:8000 &
sleep 1

curl -s http://127.0.0.1:18002/livez && echo
curl -s http://127.0.0.1:18002/orders && echo

kill %1
```

---

## Phase 14 — Security scans (Trivy + Gitleaks)

### Gitleaks (full history scan)

```bash
gitleaks detect --source . --config .gitleaks.toml --redact --no-banner
```

### Trivy — container image scan

```bash
trivy image --severity CRITICAL,HIGH --ignore-unfixed users-service:test
```

```bash
trivy image --severity CRITICAL,HIGH --ignore-unfixed products-service:test
```

```bash
trivy image --severity CRITICAL,HIGH --ignore-unfixed orders-service:test
```

```bash
trivy image --format sarif --output trivy-users.sarif users-service:test
```

```bash
trivy image --format spdx-json --output spdx-users.json users-service:test
```

### Conftest — OPA policy enforcement

```bash
conftest test k8s/apps/
```

```bash
conftest test --policy k8s/policies/conftest/ k8s/apps/
```

---

## Phase 15 — Kustomize (manifest rendering)

```bash
kustomize build k8s/apps/ | head -50
```

```bash
kustomize build k8s/apps/overlays/dev/ | head -50
```

```bash
kustomize build k8s/apps/overlays/staging/ | head -50
```

```bash
kustomize build k8s/apps/overlays/prod/ | head -50
```

```bash
kustomize build k8s/argocd/install/ | head -30
```

---

## Phase 16 — Helm (observability stack)

```bash
helm list -A
```

```bash
helm get values -n mitos prometheus
```

```bash
helm get values -n monitoring grafana
```

```bash
helm get values -n monitoring elasticsearch
```

```bash
helm get values -n monitoring kibana
```

---

## Phase 17 — Network connectivity tests

```bash
kubectl run test-curl --rm -i --restart=Never --image=curlimages /curl:latest -- curl -s -o /dev/null -w "%{http_code}" http://users-service.devops-platform.svc.cluster.local:/livez
```

```bash
kubectl run test-curl --rm -i --restart=Never --image=curlimages/curl:latest -- curl -s http://vault-service.vault.svc.cluster.local:8200/v1/sys/health
```

```bash
kubectl run test-curl --rm -i --restart=Never --image=curlimages/curl:latest -- curl -s http://elasticsearch.monitoring.svc.cluster.local:9200/
```

---

## Phase 18 — Validation scripts (Phase 7 from docs)

```bash
./scripts/validate-platform.sh --skip-incident
```

```bash
./scripts/validate-platform.sh --ci
```

```bash
./scripts/validate-platform.sh --only 1,2,5
```

```bash
./scripts/validate-security.sh
```

```bash
./scripts/validate-security.sh --ci
```

```bash
./scripts/bootstrap-vault-secret.sh
```

```bash
./scripts/bootstrap-elasticsearch-secret.sh
```

---

## Phase 19 — CI/CD pipeline simulation (local)

### CI commands to simulate GitHub Actions job

```bash
ruff check app/
```

```bash
terraform fmt -check -recursive terraform/
```

```bash
terraform -chdir=terraform init -backend=false && terraform -chdir=terraform validate
```

```bash
yamllint -s k8s/ ansible/
```

```bash
kubeconform -skip
```

```bash
pip install pytest httpx pip-audit
pip install -e app/shared/
for svc in users-service products-service orders-service; do
  pip install -r app/$svc/requirements.txt
done
```

```bash
pip-audit -r app/shared/requirements.txt -r app/users-service/requirements.txt -r app/products-service/requirements.txt -r app/orders-service/requirements.txt --strict
```

```bash
for svc in users-service products-service orders-service; do
  (cd "app/$svc" && PYTHONPATH=../shared:. ENVIRONMENT=dev python -c "import main")
done
```

```bash
gitleaks detect --source . --config .gitleaks.toml --no-banner --redact
```

---

## Phase 20 — End-to-end summary (run at the end for final report)

```sh
echo "═══════════════════════════════════════════"
echo "  DevOps Central Platform - Test Report"
echo "═══════════════════════════════════════════"
echo ""

echo "── Terraform ──"
terraform -chdir=terraform validate && echo "✅ Terraform" || echo "❌ Terraform"

echo "── Ansible ──"
ansible-playbook ansible/playbook.yml --syntax-check >/dev/null 2>&1 && echo "✅ Ansible" || echo "❌ Ansible"

echo "── K8s ──"
kubectl cluster-info >/dev/null 2>&1 && echo "✅ Kubernetes" || echo "❌ Kubernetes"

echo "── Pods ──"
kubectl get pods -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase --no-headers

echo "── Microservices ──"
for svc in users-service products-service orders-service; do
   READY=$(kubectl -n devops-platform get deploy $svc -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)
   [ "$READY" -ge 1 ] && echo "✅ $svc ($READY)" || echo "❌ $svc ($READY)"
done

echo "── Vault ──"
kubectl qualfault-c4b5cdb8b-rphwh -- vault status | grep -qs 'Sealed.*false' && echo "✅ Vault" || echo "❌ Vault"

echo "── Monitoring ──"
for pod in prometheus grafan alertmanager elasticsearch kibana; do
  kubectl -n monitoring get pods -l "app=$app" -o jsonpath='{.items[0].status.phase}' | grep -q Running && echo "✅ $pod" || echo "❌ $pod"
done

echo ""
echo "═══════════════════════════════════════════"
```