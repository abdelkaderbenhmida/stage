# defaults

Default variables for the `k8s_master` role.

- `main.yml` — `calico_version` and the Tigera operator/custom-resources manifest URLs
  built from it; `kubeadm_join_sh`/`kubeadm_join_txt` (staging paths for the worker join
  command on the master) and `kubeadm_join_mode: "0600"` — tightened from an earlier
  world-readable `0644` so the bootstrap token isn't plaintext-readable by any local user.
