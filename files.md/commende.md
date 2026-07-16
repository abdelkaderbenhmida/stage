All green. Run these to verify yourself:
ssh devops@192.168.56.10
kubectl get nodes          # 3 Ready
kubectl get pods -A        # all Running
kubectl cluster-info       # API + CoreDNS
Or from your host machine:
ansible -m shell -a 'kubectl get nodes' -i inventory.ini all
▣  Build · DeepSeek V4 Flash Free · 58.4s
give other commende i can do it 
+ Thought: 768ms
# Deploy test app
kubectl create deployment nginx --image=nginx --replicas=3
kubectl expose deployment nginx --port=80 --type=NodePort
# Get access URL
kubectl get svc nginx -o jsonpath='http://{.spec.ports[0].nodePort}'

# Scale test
kubectl scale deployment nginx --replicas=5
kubectl get pods -o wide

# Storage test
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 1Gi } }
EOF
kubectl get pvc

# Node resources
kubectl top nodes
kubectl top pods -A

# Check worker -> pod routing
kubectl get pods -l app=nginx -o wide
# Then curl the pod IP from worker-01/02

# Cluster events
kubectl get events --sort-by='.lastTimestamp' | tail -20

# API resources
kubectl api-resources | head -20

# Node details
kubectl describe nodes | grep -E "Addresses|Capacity|Conditions" -A5