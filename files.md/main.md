Updated todo list

Here is the file main.tf explained top to bottom.

1. The first block declares the Terraform setup itself.
It says this configuration needs Terraform 1.5 or newer, and it declares two providers: `libvirt` for creating virtual machines on a local hypervisor, and `local` for writing a generated file on disk.

2. The `provider "libvirt"` block tells Terraform how to connect to the virtualization layer.
It uses the value from `var.libvirt_uri`, so the connection target is configurable instead of hardcoded.

3. The `locals` block builds the node inventory in memory.
`master_node` defines one master VM with a fixed name, a fixed IP inside the private subnet, and the role label `master`.

4. Still in `locals`, `worker_nodes` creates a list of workers.
It loops from 0 to `var.worker_count - 1`, assigns each worker a name like `worker-01`, `worker-02`, and gives each one a unique IP in the same subnet.

5. The `nodes` map combines the master and workers into one structure.
That makes it easy to use the same resources for all machines with `for_each`.

6. The `libvirt_network "platform"` resource creates the private network for the cluster.
It uses NAT mode, a configurable domain name, and the subnet from `var.network_cidr`. DHCP and DNS are enabled so the VMs can resolve and communicate on that network.

7. The `libvirt_volume "base"` resource defines the base disk image.
It points to an existing cloud image and clones it into the selected storage pool, so each VM can be created from that base.

8. The `libvirt_volume "node"` resource creates one disk per node.
Because it uses `for_each = local.nodes`, Terraform makes one volume for the master and one for each worker. Each volume is cloned from the base image and sized according to `var.disk_size_gb`.

9. The `libvirt_cloudinit_disk "init"` resource creates a cloud-init ISO for each node.
This is where Terraform injects startup configuration into each VM.

10. Inside `user_data`, the `cloud-init.tpl` template is rendered.
It passes the hostname, SSH user, and SSH public key into the template so the VM can be initialized with the correct identity and access.

11. Inside `network_config`, the `network-config.tpl` template is rendered.
It injects the guest interface name, static IP, subnet prefix, gateway, and DNS servers so each VM boots with a fixed network configuration.

12. The `libvirt_domain "node"` resource creates the actual virtual machines.
Again, `for_each = local.nodes` means one VM for the master and one for each worker.

13. The `name`, `memory`, and `vcpu` settings define the VM identity and sizing.
Each machine gets the configured amount of RAM and CPU cores from variables.

14. The `cloudinit` line attaches the cloud-init ISO to the VM.
That is how the VM receives its hostname, user account, SSH key, and network settings on first boot.

15. The `disk` block attaches the cloned virtual disk to the VM.
It uses the per-node disk volume created earlier.

16. The `network_interface` block connects the VM to the private Terraform network.
It also sets the hostname inside the virtual network.

17. The `console` block enables serial console access.
That is useful for debugging boot issues or reaching the VM without SSH.

18. The `graphics` block enables a SPICE graphical console.
This is mainly for local VM access and troubleshooting.

19. The `local_file "ansible_inventory"` resource writes an Ansible inventory file.
Terraform renders `inventory.tpl` and saves the result as `inventory.ini` inside the Terraform directory.

20. In that inventory template, the master and workers are written into groups.
The rendered file can then be used directly by Ansible to target the right hosts.

In one sentence: this file provisions the VMs, places them on a private network, boots them with cloud-init, and generates the Ansible inventory automatically.

If you want, I can do the same line-by-line explanation for variables.tf next.