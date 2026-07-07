gadour@gadour-LOQ-15IAX9:~/Desktop/stage$ ssh devops@192.168.56.10
kubectl get nodes          # 3 Ready
kubectl get pods -A        # all Running
kubectl cluster-info   
Welcome to Ubuntu 22.04.5 LTS (GNU/Linux 5.15.0-185-generic x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/pro

 System information as of Tue Jul  7 15:12:04 UTC 2026

  System load:  0.12               Processes:               195
  Usage of /:   25.4% of 19.20GB   Users logged in:         1
  Memory usage: 51%                IPv4 address for enp1s0: 192.168.56.10
  Swap usage:   0%

 * Strictly confined Kubernetes makes edge and IoT secure. Learn how MicroK8s
   just raised the bar for easy, resilient and secure K8s cluster deployment.

   https://ubuntu.com/engage/secure-kubernetes-at-the-edge

Expanded Security Maintenance for Applications is not enabled.

1 update can be applied immediately.
To see these additional updates run: apt list --upgradable

Enable ESM Apps to receive additional future security updates.
See https://ubuntu.com/esm or run: sudo pro status

New release '24.04.4 LTS' available.
Run 'do-release-upgrade' to upgrade to it.


Last login: Tue Jul  7 15:04:01 2026 from 192.168.56.1
devops@master-01:~$ kubectl get nodes
NAME        STATUS   ROLES           AGE   VERSION
master-01   Ready    control-plane   14m   v1.28.15
worker-01   Ready    <none>          14m   v1.28.15
worker-02   Ready    <none>          13m   v1.28.15
devops@master-01:~$ kubectl get pods -A
NAMESPACE          NAME                                       READY   STATUS    RESTARTS   AGE
calico-apiserver   calico-apiserver-5bc6c89b97-49jn5          1/1     Running   0          11m
calico-apiserver   calico-apiserver-5bc6c89b97-frbnj          1/1     Running   0          11m
calico-system      calico-kube-controllers-5c57976dbd-4j6hr   1/1     Running   0          14m
calico-system      calico-node-592ql                          1/1     Running   0          14m
calico-system      calico-node-75ghq                          1/1     Running   0          13m
calico-system      calico-node-927p5                          1/1     Running   0          14m
calico-system      calico-typha-5b764d7465-6th8p              1/1     Running   0          13m
calico-system      calico-typha-5b764d7465-cjcsq              1/1     Running   0          14m
calico-system      csi-node-driver-7v8h2                      2/2     Running   0          13m
calico-system      csi-node-driver-f25zk                      2/2     Running   0          14m
calico-system      csi-node-driver-zdx7g                      2/2     Running   0          14m
kube-system        coredns-5dd5756b68-6wcfv                   1/1     Running   0          14m
kube-system        coredns-5dd5756b68-9r2fv                   1/1     Running   0          14m
kube-system        etcd-master-01                             1/1     Running   10         14m
kube-system        kube-apiserver-master-01                   1/1     Running   10         14m
kube-system        kube-controller-manager-master-01          1/1     Running   11         14m
kube-system        kube-proxy-4x57v                           1/1     Running   0          13m
kube-system        kube-proxy-bkgn4                           1/1     Running   0          14m
kube-system        kube-proxy-cqsbk                           1/1     Running   0          14m
kube-system        kube-scheduler-master-01                   1/1     Running   12         14m
tigera-operator    tigera-operator-94d7f7696-v2vns            1/1     Running   0          14m
devops@master-01:~$ kubectl cluster-info 
Kubernetes control plane is running at https://192.168.56.10:6443
CoreDNS is running at https://192.168.56.10:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy

To further debug and diagnose cluster problems, use 'kubectl cluster-info dump'.
devops@master-01:~$ kubectl create deployment nginx --image==nginx --replicas=3
error: failed to create deployment: Deployment.apps "nginx" is invalid: spec.template.spec.containers[0].name: Invalid value: "=nginx": a lowercase RFC 1123 label must consist of lower case alphanumeric characters or '-', and must start and end with an alphanumeric character (e.g. 'my-name',  or '123-abc', regex used for validation is '[a-z0-9]([-a-z0-9]*[a-z0-9])?')
devops@master-01:~$ kubectl create deployment nginx --image=nginx --replicas=3
deployment.apps/nginx created
devops@master-01:~$ kubectl expose deployment nginx --port=80 --type=NodePort
service/nginx exposed
devops@master-01:~$ kubectl get svc nginx -o jsonpath='http://{.spec.ports[0].nodePort}'
http://31282devops@master-01:~$ 
devops@master-01:~$ 
devops@master-01:~$ 
devops@master-01:~$ kubectl get svc nginx -o jsonpath='http://{.spec.ports[0].nodePort}'
http://31282devops@master-01:~$ kubectl scale nginx --replicas=5--replicas=5
kubectl get pods -o wide
deployment.apps/nginx scaled
NAME                     READY   STATUS              RESTARTS   AGE     IP               NODE        NOMINATED NODE   READINESS GATES
nginx-7854ff8877-mr6g5   1/1     Running             0          5m15s   192.168.37.195   worker-02   <none>           <none>
nginx-7854ff8877-nqf59   0/1     ContainerCreating   0          0s      <none>           worker-02   <none>           <none>
nginx-7854ff8877-ns9lt   0/1     ContainerCreating   0          0s      <none>           worker-01   <none>           <none>
nginx-7854ff8877-p2c5h   1/1     Running             0          5m15s   192.168.171.4    worker-01   <none>           <none>
nginx-7854ff8877-tf6bw   1/1     Running             0          5m15s   192.168.171.5    worker-01   <none>           <none>
devops@master-01:~$ kubectl scale deployment  nginx --replicas=5
deployment.apps/nginx scaled
devops@master-01:~$ kubectl get pods -o wide
NAME                     READY   STATUS    RESTARTS   AGE     IP               NODE        NOMINATED NODE   READINESS GATES
nginx-7854ff8877-mr6g5   1/1     Running   0          5m25s   192.168.37.195   worker-02   <none>           <none>
nginx-7854ff8877-nqf59   1/1     Running   0          10s     192.168.37.196   worker-02   <none>           <none>
nginx-7854ff8877-ns9lt   1/1     Running   0          10s     192.168.171.6    worker-01   <none>           <none>
nginx-7854ff8877-p2c5h   1/1     Running   0          5m25s   192.168.171.4    worker-01   <none>           <none>
nginx-7854ff8877-tf6bw   1/1     Running   0          5m25s   192.168.171.5    worker-01   <none>           <none>
devops@master-01:~$ kubectl get pods -o wide
NAME                     READY   STATUS    RESTARTS   AGE     IP               NODE        NOMINATED NODE   READINESS GATES
nginx-7854ff8877-mr6g5   1/1     Running   0          5m35s   192.168.37.195   worker-02   <none>           <none>
nginx-7854ff8877-nqf59   1/1     Running   0          20s     192.168.37.196   worker-02   <none>           <none>
nginx-7854ff8877-ns9lt   1/1     Running   0          20s     192.168.171.6    worker-01   <none>           <none>
nginx-7854ff8877-p2c5h   1/1     Running   0          5m35s   192.168.171.4    worker-01   <none>           <none>
nginx-7854ff8877-tf6bw   1/1     Running   0          5m35s   192.168.171.5    worker-01   <none>           <none>
devops@master-01:~$ kubectl apply -f - <<EOF
> kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 1Gi } }
EOF
error: error validating "STDIN": error validating data: apiVersion not set; if you choose to ignore these errors, turn validation off with --validate=false
devops@master-01:~$ kubectl apply -f - <<EOF
kind: PersistentVolumeClaim
metadata:
  name: test-pvc
spec:
  accessModes: [ReadWriteOnce]
  resources: { requests: { storage: 1Gi } }
EOF
error: error validating "STDIN": error validating data: apiVersion not set; if you choose to ignore these errors, turn validation off with --validate=false
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
devops@master-01:~$ kubectl get pods -l app=nginx -o wide
NAME                     READY   STATUS    RESTARTS   AGE     IP               NODE        NOMINATED NODE   READINESS GATES
nginx-7854ff8877-mr6g5   1/1     Running   0          10m     192.168.37.195   worker-02   <none>           <none>
nginx-7854ff8877-nqf59   1/1     Running   0          4m46s   192.168.37.196   worker-02   <none>           <none>
nginx-7854ff8877-ns9lt   1/1     Running   0          4m46s   192.168.171.6    worker-01   <none>           <none>
nginx-7854ff8877-p2c5h   1/1     Running   0          10m     192.168.171.4    worker-01   <none>           <none>
nginx-7854ff8877-tf6bw   1/1     Running   0          10m     192.168.171.5    worker-01   <none>           <none>
devops@master-01:~$ kubectl get events --sort-by='.lastTimestamp' | tail -20
8m56s       Normal    Started                   pod/nginx-7854ff8877-p2c5h       Started container nginx
8m56s       Normal    Created                   pod/nginx-7854ff8877-p2c5h       Created container nginx
8m56s       Normal    Pulled                    pod/nginx-7854ff8877-p2c5h       Successfully pulled image "nginx" in 1m19.603s (1m19.603s including waiting)
8m55s       Normal    Pulled                    pod/nginx-7854ff8877-tf6bw       Successfully pulled image "nginx" in 1.443s (1m21.029s including waiting)
8m55s       Normal    Created                   pod/nginx-7854ff8877-tf6bw       Created container nginx
8m55s       Normal    Started                   pod/nginx-7854ff8877-tf6bw       Started container nginx
5m1s        Normal    SuccessfulCreate          replicaset/nginx-7854ff8877      Created pod: nginx-7854ff8877-nqf59
5m1s        Normal    Pulling                   pod/nginx-7854ff8877-ns9lt       Pulling image "nginx"
5m1s        Normal    ScalingReplicaSet         deployment/nginx                 Scaled up replica set nginx-7854ff8877 to 5 from 3
5m1s        Normal    SuccessfulCreate          replicaset/nginx-7854ff8877      Created pod: nginx-7854ff8877-ns9lt
5m1s        Normal    Scheduled                 pod/nginx-7854ff8877-nqf59       Successfully assigned default/nginx-7854ff8877-nqf59 to worker-02
5m1s        Normal    Pulling                   pod/nginx-7854ff8877-nqf59       Pulling image "nginx"
5m1s        Normal    Scheduled                 pod/nginx-7854ff8877-ns9lt       Successfully assigned default/nginx-7854ff8877-ns9lt to worker-01
4m59s       Normal    Started                   pod/nginx-7854ff8877-ns9lt       Started container nginx
4m59s       Normal    Pulled                    pod/nginx-7854ff8877-ns9lt       Successfully pulled image "nginx" in 1.581s (1.581s including waiting)
4m59s       Normal    Created                   pod/nginx-7854ff8877-ns9lt       Created container nginx
4m56s       Normal    Started                   pod/nginx-7854ff8877-nqf59       Started container nginx
4m56s       Normal    Created                   pod/nginx-7854ff8877-nqf59       Created container nginx
4m56s       Normal    Pulled                    pod/nginx-7854ff8877-nqf59       Successfully pulled image "nginx" in 4.693s (4.693s including waiting)
10s         Normal    FailedBinding             persistentvolumeclaim/test-pvc   no persistent volumes available for this claim and no storage class is set
devops@master-01:~$ kubectl api-resources | head -20
NAME                              SHORTNAMES                                      APIVERSION                             NAMESPACED   KIND
bindings                                                                          v1                                     true         Binding
componentstatuses                 cs                                              v1                                     false        ComponentStatus
configmaps                        cm                                              v1                                     true         ConfigMap
endpoints                         ep                                              v1                                     true         Endpoints
events                            ev                                              v1                                     true         Event
limitranges                       limits                                          v1                                     true         LimitRange
namespaces                        ns                                              v1                                     false        Namespace
nodes                             no                                              v1                                     false        Node
persistentvolumeclaims            pvc                                             v1                                     true         PersistentVolumeClaim
persistentvolumes                 pv                                              v1                                     false        PersistentVolume
pods                              po                                              v1                                     true         Pod
podtemplates                                                                      v1                                     true         PodTemplate
replicationcontrollers            rc                                              v1                                     true         ReplicationController
resourcequotas                    quota                                           v1                                     true         ResourceQuota
secrets                                                                           v1                                     true         Secret
serviceaccounts                   sa                                              v1                                     true         ServiceAccount
services                          svc                                             v1                                     true         Service
mutatingwebhookconfigurations                                                     admissionregistration.k8s.io/v1        false        MutatingWebhookConfiguration
validatingwebhookconfigurations                                                   admissionregistration.k8s.io/v1        false        ValidatingWebhookConfiguration
devops@master-01:~$ kubectl describe nodes | grep -E "Addresses|Capacity|Conditions" -A5
Conditions:
  Type                 Status  LastHeartbeatTime                 LastTransitionTime                Reason                       Message
  ----                 ------  -----------------                 ------------------                ------                       -------
  NetworkUnavailable   False   Tue, 07 Jul 2026 14:58:42 +0000   Tue, 07 Jul 2026 14:58:42 +0000   CalicoIsUp                   Calico is running on this node
  MemoryPressure       False   Tue, 07 Jul 2026 15:25:10 +0000   Tue, 07 Jul 2026 14:58:05 +0000   KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure         False   Tue, 07 Jul 2026 15:25:10 +0000   Tue, 07 Jul 2026 14:58:05 +0000   KubeletHasNoDiskPressure     kubelet has no disk pressure
--
Addresses:
  InternalIP:  192.168.56.10
  Hostname:    master-01
Capacity:
  cpu:                2
  ephemeral-storage:  20134592Ki
  hugepages-2Mi:      0
  memory:             2010820Ki
  pods:               110
--
  Warning  InvalidDiskCapacity      31m                kubelet          invalid capacity 0 on image filesystem
  Normal   NodeHasSufficientMemory  31m (x8 over 31m)  kubelet          Node master-01 status is now: NodeHasSufficientMemory
  Normal   NodeHasNoDiskPressure    31m (x7 over 31m)  kubelet          Node master-01 status is now: NodeHasNoDiskPressure
  Normal   NodeHasSufficientPID     31m (x7 over 31m)  kubelet          Node master-01 status is now: NodeHasSufficientPID
  Normal   Starting                 31m                kubelet          Starting kubelet.
  Warning  InvalidDiskCapacity      31m                kubelet          invalid capacity 0 on image filesystem
  Normal   Starting                 31m                kubelet          Starting kubelet.
  Normal   NodeAllocatableEnforced  31m                kubelet          Updated Node Allocatable limit across pods
  Normal   NodeHasSufficientMemory  31m                kubelet          Node master-01 status is now: NodeHasSufficientMemory
  Normal   NodeHasNoDiskPressure    31m                kubelet          Node master-01 status is now: NodeHasNoDiskPressure
  Normal   NodeHasSufficientPID     31m                kubelet          Node master-01 status is now: NodeHasSufficientPID
--
Conditions:
  Type                 Status  LastHeartbeatTime                 LastTransitionTime                Reason                       Message
  ----                 ------  -----------------                 ------------------                ------                       -------
  NetworkUnavailable   False   Tue, 07 Jul 2026 15:00:38 +0000   Tue, 07 Jul 2026 15:00:38 +0000   CalicoIsUp                   Calico is running on this node
  MemoryPressure       False   Tue, 07 Jul 2026 15:25:27 +0000   Tue, 07 Jul 2026 14:58:45 +0000   KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure         False   Tue, 07 Jul 2026 15:25:27 +0000   Tue, 07 Jul 2026 14:58:45 +0000   KubeletHasNoDiskPressure     kubelet has no disk pressure
--
Addresses:
  InternalIP:  192.168.56.11
  Hostname:    worker-01
Capacity:
  cpu:                2
  ephemeral-storage:  20134592Ki
  hugepages-2Mi:      0
  memory:             2010824Ki
  pods:               110
--
  Warning  InvalidDiskCapacity      30m                kubelet          invalid capacity 0 on image filesystem
  Normal   NodeAllocatableEnforced  30m                kubelet          Updated Node Allocatable limit across pods
  Normal   NodeHasSufficientMemory  30m                kubelet          Node worker-01 status is now: NodeHasSufficientMemory
  Normal   NodeHasNoDiskPressure    30m                kubelet          Node worker-01 status is now: NodeHasNoDiskPressure
  Normal   NodeHasSufficientPID     30m                kubelet          Node worker-01 status is now: NodeHasSufficientPID
  Normal   NodeReady                30m                kubelet          Node worker-01 status is now: NodeReady
--
Conditions:
  Type                 Status  LastHeartbeatTime                 LastTransitionTime                Reason                       Message
  ----                 ------  -----------------                 ------------------                ------                       -------
  NetworkUnavailable   False   Tue, 07 Jul 2026 15:01:22 +0000   Tue, 07 Jul 2026 15:01:22 +0000   CalicoIsUp                   Calico is running on this node
  MemoryPressure       False   Tue, 07 Jul 2026 15:25:12 +0000   Tue, 07 Jul 2026 14:59:31 +0000   KubeletHasSufficientMemory   kubelet has sufficient memory available
  DiskPressure         False   Tue, 07 Jul 2026 15:25:12 +0000   Tue, 07 Jul 2026 14:59:31 +0000   KubeletHasNoDiskPressure     kubelet has no disk pressure
--
Addresses:
  InternalIP:  192.168.56.12
  Hostname:    worker-02
Capacity:
  cpu:                2
  ephemeral-storage:  20134592Ki
  hugepages-2Mi:      0
  memory:             2010820Ki
  pods:               110
--
  Warning  InvalidDiskCapacity      30m                kubelet          invalid capacity 0 on image filesystem
  Normal   NodeAllocatableEnforced  30m                kubelet          Updated Node Allocatable limit across pods
  Normal   NodeHasSufficientMemory  30m                kubelet          Node worker-02 status is now: NodeHasSufficientMemory
  Normal   NodeHasNoDiskPressure    30m                kubelet          Node worker-02 status is now: NodeHasNoDiskPressure
  Normal   NodeHasSufficientPID     30m                kubelet          Node worker-02 status is now: NodeHasSufficientPID
  Normal   RegisteredNode           29m                node-controller  Node worker-02 event: Registered Node worker-02 in Controller
devops@master-01:~$ 