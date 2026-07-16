devops@master-01:~$ kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
EOF
persistentvolumeclaim/test-pvc created
devops@master-01:~$ kubectl get pvc
NAME       STATUS    VOLUME   CAPACITY   ACCESS MODES   STORAGECLASS   AGE
test-pvc   Pending                                                     15s
devops@master-01:~$ kubectl get pvc
NAME       STATUS    VOLUME   CAPACITY   ACCESS MODES   STORAGECLASS   AGE
test-pvc   Pending                                                     18s
devops@master-01:~$ kubectl top nodes
kubectl top pods -A
error: Metrics API not available
error: Metrics API not available
devops@master-01:~$ kubectl top nodes
error: Metrics API not available
devops@master-01:~$ kubectl top nodes
error: Metrics API not available