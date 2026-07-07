# Phase 3 — Conteneurisation des microservices

**Objectif :** empaqueter les 3 microservices FastAPI (Users, Products, Orders) en images Docker prêtes à déployer sur le cluster Kubernetes.

**Critère de validation :**
```bash
kubectl get pods -n devops-platform
```
→ tous les Pods sont `Running` avec 2/2 ou 1/1 containers prêts.

---

## Structure cible

```
app/
├── users-service/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── products-service/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
└── orders-service/
    ├── main.py
    ├── requirements.txt
    └── Dockerfile

k8s/
└── apps/
    ├── users-deployment.yaml
    ├── users-service.yaml
    ├── products-deployment.yaml
    ├── products-service.yaml
    ├── orders-deployment.yaml
    ├── orders-service.yaml
    ├── hpa.yaml
    └── rbac.yaml
```

---

## Étape 1 — Créer le code de chaque microservice

Pour chaque service (`users-service`, `products-service`, `orders-service`), créer un fichier `main.py` avec FastAPI.

Contenu attendu :
- Routes API minimales (CRUD de base)
- Endpoint `/health` → `{"status": "healthy"}`
- Endpoint `/metrics` → métriques Prometheus (compteurs de requêtes, latence, erreurs)
- Endpoint racine `/` → info sur le service (nom + version)
- Port d'écoute : `8000`

Exemple minimal pour `users-service/main.py` :
```python
from fastapi import FastAPI
from prometheus_client import Counter, generate_latest
from fastapi.responses import PlainTextResponse

app = FastAPI(title="Users Service", version="1.0.0")
REQUEST_COUNT = Counter("users_requests_total", "Total requests")

@app.get("/")
def root():
    REQUEST_COUNT.inc()
    return {"service": "users", "version": "1.0.0"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/users")
def list_users():
    REQUEST_COUNT.inc()
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")
```

Faire pareil pour `products-service` (routes `/products`) et `orders-service` (routes `/orders`).

---

## Étape 2 — Créer le fichier `requirements.txt` pour chaque service

Contenu identique pour les 3 services :

```
fastapi==0.104.1
uvicorn[standard]==0.24.0
prometheus-client==0.17.1
```

---

## Étape 3 — Créer un `Dockerfile` multi-stage pour chaque service

Exemple pour `users-service/Dockerfile` :

```dockerfile
# Stage 1 — build dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2 — final image
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY main.py .
ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Reproduire pour `products-service` et `orders-service` (le code `main.py` change, le reste est identique).

---

## Étape 4 — Construire et tester chaque image en local

```bash
# Build
docker build -t users-service:local ./app/users-service
docker build -t products-service:local ./app/products-service
docker build -t orders-service:local ./app/orders-service

# Test — chaque service doit répondre sur /health
docker run -d -p 8001:8000 users-service:local
docker run -d -p 8002:8000 products-service:local
docker run -d -p 8003:8000 orders-service:local

# Vérification
curl http://localhost:8001/health   # → {"status":"healthy"}
curl http://localhost:8002/health   # → {"status":"healthy"}
curl http://localhost:8003/health   # → {"status":"healthy"}
curl http://localhost:8001/metrics  # → métriques Prometheus

# Arrêt
docker stop $(docker ps -q)
```

**Ne pas continuer tant que les 3 services ne répondent pas.**

---

## Étape 5 — Pousser les images sur un registre

Sur le master (où Docker est installé), tagger + push vers Docker Hub ou GHCR :

```bash
# Exemple avec Docker Hub
docker tag users-service:local    <user>/users-service:latest
docker tag products-service:local <user>/products-service:latest
docker tag orders-service:local   <user>/orders-service:latest

docker login
docker push <user>/users-service:latest
docker push <user>/products-service:latest
docker push <user>/orders-service:latest
```

Alternative : utiliser le registry local du cluster ou construire directement sur le master via `docker build`.

---

## Étape 6 — Créer les manifests Kubernetes (Deployment + Service)

Créer le dossier `k8s/apps/` avec, pour chaque microservice, un Deployment (2 replicas) et un Service.

Exemple `k8s/apps/users-deployment.yaml` :
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: users-service
  namespace: devops-platform
spec:
  replicas: 2
  selector:
    matchLabels:
      app: users-service
  template:
    metadata:
      labels:
        app: users-service
    spec:
      containers:
      - name: users-service
        image: <user>/users-service:latest
        ports:
        - containerPort: 8000
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
          limits:
            cpu: "250m"
            memory: "256Mi"
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: users-service
  namespace: devops-platform
spec:
  selector:
    app: users-service
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

Reproduire la même structure pour `products-service` et `orders-service`.

---

## Étape 7 — Ajouter les ressources optionnelles (HPA + RBAC)

`k8s/apps/hpa.yaml` — Horizontal Pod Autoscaler pour chaque service :
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: users-service
  namespace: devops-platform
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: users-service
  minReplicas: 2
  maxReplicas: 5
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
```

`k8s/apps/rbac.yaml` — RBAC minimal pour le namespace `devops-platform` (ServiceAccount + Role + RoleBinding).

---

## Étape 8 — Déployer sur le cluster

```bash
# Créer le namespace
kubectl create namespace devops-platform

# Appliquer les manifests
kubectl apply -f k8s/apps/

# Vérifier
kubectl get pods -n devops-platform -o wide
kubectl get svc -n devops-platform
kubectl get hpa -n devops-platform
```

---

## Étape 9 — Vérification finale

```bash
# Tous les Pods doivent être Running
kubectl get pods -n devops-platform

# Tester un service depuis le cluster
kubectl run -it --rm curl-test --image=curlimages/curl --restart=Never -- \
  curl http://users-service.devops-platform.svc.cluster.local/health

# Vérifier les métriques
kubectl run -it --rm curl-test --image=curlimages/curl --restart=Never -- \
  curl http://users-service.devops-platform.svc.cluster.local/metrics

# Vérifier le self-healing
kubectl delete pod -l app=users-service -n devops-platform
kubectl get pods -n devops-platform -w   # → Pod recréé en <30s
```

---

## Checklist de validation Phase 3

- [ ] 3 microservices FastAPI créés (Users, Products, Orders)
- [ ] Chaque service a `/health` et `/metrics`
- [ ] Dockerfile multi-stage pour chaque service
- [ ] Images construites et testées en local (`docker run` + `curl /health`)
- [ ] Images poussées sur un registre accessible
- [ ] Namespace `devops-platform` créé
- [ ] 3 Deployments avec 2 replicas chacun
- [ ] 3 Services (ClusterIP) créés
- [ ] `resources.requests` + `resources.limits` sur chaque container
- [ ] `readinessProbe` + `livenessProbe` sur chaque container
- [ ] HPA configuré pour chaque service
- [ ] `kubectl get pods -n devops-platform` → tous Running
- [ ] Suppression d'un Pod → recréation automatique < 30s
```
