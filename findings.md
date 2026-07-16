host@hostname:~/Desktop/stage$ semgrep ci
                  
                  
┌────────────────┐
│ Debugging Info │
└────────────────┘
                  
  SCAN ENVIRONMENT
  versions    - semgrep 1.168.0 on python 3.12.3                       
  environment - running in environment git, triggering event is unknown
            
  CONNECTION
Unable to infer repo_url. `semgrep ci` must be run from within a git repository with a remote origin defined. Set SEMGREP_REPO_URL to override, or use `semgrep scan` instead.
  Initializing scan (deployment=abdelkader-benhmida-supcom-tn, scan_id=192… 
  Enabled products: Supply Chain, Code                                                                                                                  
        
        
  ENGINE
Semgrep Pro Engine will be installed in /home/gadour/.local/lib/python3.12/site-packages/semgrep/bin/semgrep-core-proprietary
Downloading... ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 314.7/314.7 MB 2.0 MB/s 0:00:00                                                                            

Successfully installed Semgrep Pro Engine (version 1.168.0)!
⠧ Loading rules...                                                                                                                                      
  Scanning 67 files (only git-tracked) with 2944 Code rules, 17206 Supply   
  Chain rules:                                                              
            
  CODE RULES
                                                                            
  Language      Rules   Files          Origin      Rules                    
 ─────────────────────────────        ───────────────────                   
  <multilang>      49      67          Pro rules    1869                    
  yaml             35      17          Community    1075                    
  python         1156       7                                               
  terraform       101       5                                               
  php              65       3                                               
  dockerfile        6       3                                               
  bash              4       1                                               
                                                                            
                    
  SUPPLY CHAIN RULES
                                                                            
  Dependency Sources   Resolution Method   Ecosystem   Dependencies   Rules 
 ───────────────────────────────────────────────────────────────────────────
  app/orders-service   Lockfile            Pypi        4              17206 
  /requirements.txt                                                         
  app/products-servi   Lockfile            Pypi        4              17206 
  ce/requirements.tx                                                        
  t                                                                         
  app/shared/require   Lockfile            Pypi        4              17206 
  ments.txt                                                                 
  app/users-service/   Lockfile            Pypi        4              17206 
  requirements.txt                                                          
                                                                            
                                                                            
  Analysis       Rules                                                      
 ──────────────────────                                                     
  Malicious      11485                                                      
  Basic           4423                                                      
  Reachability    1298                                                      
                                                                            
          
  PROGRESS
   
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:00                                                                            
  Uploading scan results  
  Finalizing scan                                                                                     
                                 
                                 
┌───────────────────────────────┐
│ 31 Non-blocking Code Findings │
└───────────────────────────────┘
                                              
    .github/workflows/ci-cd.yml
    ❯❱ yaml.github-actions.security.github-actions-mutable-action-
       tag.github-actions-mutable-action-tag                      
          GitHub Actions step uses a mutable tag or branch        
          reference. Tags and branch names can be silently        
          repointed by the action owner, enabling supply-chain    
          attacks — as seen in the trivy-action and kics-github-  
          action compromises. Pin the reference to a full         
          40-character commit SHA instead, e.g. `uses: actions/che
          ckout@8ade135a41bc03ea155e62e844d188df1ea18608`.        
          Details: https://sg.run/2LgAL                           
                                                                  
           21┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
           23┆ - uses: actions/setup-python@v5
            ⋮┆----------------------------------------
           54┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
           59┆ uses: gitleaks/gitleaks-action@v2
            ⋮┆----------------------------------------
           75┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
           77┆ - uses: actions/setup-python@v5
            ⋮┆----------------------------------------
          116┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
          119┆ uses: docker/setup-buildx-action@v3
            ⋮┆----------------------------------------
          122┆ uses: docker/login-action@v3
            ⋮┆----------------------------------------
          130┆ uses: docker/metadata-action@v5
            ⋮┆----------------------------------------
          139┆ uses: docker/build-push-action@v5
            ⋮┆----------------------------------------
          175┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
          178┆ uses: docker/login-action@v3
            ⋮┆----------------------------------------
          191┆ uses: aquasecurity/trivy-action@master
            ⋮┆----------------------------------------
          201┆ uses: aquasecurity/trivy-action@master
            ⋮┆----------------------------------------
          209┆ uses: actions/upload-artifact@v4
            ⋮┆----------------------------------------
          222┆ - uses: actions/checkout@v4
            ⋮┆----------------------------------------
          224┆ - uses: hashicorp/setup-terraform@v3
                                                
    app/orders-service/Dockerfile
   ❯❯❱ dockerfile.security.missing-user.missing-user
          By not specifying a USER, a program in the container may
          run as 'root'. This is a security hazard. If an attacker
          can control a process running as root, they may have    
          control over the container. Ensure that the last USER in
          a Dockerfile is a USER other than 'root'.               
          Details: https://sg.run/Gbvn                            
                                                                  
           ▶▶┆ Autofix ▶ USER non-root CMD ["uvicorn", "main:app", "--host",
              "0.0.0.0", "--port", "8000"]                                  
           14┆ CMD ["uvicorn", "main:app", "--host",
               "0.0.0.0", "--port", "8000"]         
                                                  
    app/products-service/Dockerfile
   ❯❯❱ dockerfile.security.missing-user.missing-user
          By not specifying a USER, a program in the container may
          run as 'root'. This is a security hazard. If an attacker
          can control a process running as root, they may have    
          control over the container. Ensure that the last USER in
          a Dockerfile is a USER other than 'root'.               
          Details: https://sg.run/Gbvn                            
                                                                  
           ▶▶┆ Autofix ▶ USER non-root CMD ["uvicorn", "main:app", "--host",
              "0.0.0.0", "--port", "8000"]                                  
           12┆ CMD ["uvicorn", "main:app", "--host",
               "0.0.0.0", "--port", "8000"]         
                                               
    app/users-service/Dockerfile
   ❯❯❱ dockerfile.security.missing-user.missing-user
          By not specifying a USER, a program in the container may
          run as 'root'. This is a security hazard. If an attacker
          can control a process running as root, they may have    
          control over the container. Ensure that the last USER in
          a Dockerfile is a USER other than 'root'.               
          Details: https://sg.run/Gbvn                            
                                                                  
           ▶▶┆ Autofix ▶ USER non-root CMD ["uvicorn", "main:app", "--host",
              "0.0.0.0", "--port", "8000"]                                  
           12┆ CMD ["uvicorn", "main:app", "--host",
               "0.0.0.0", "--port", "8000"]         
                                                  
    k8s/apps/orders-deployment.yaml
     ❱ yaml.kubernetes.security.run-as-non-root.run-as-non-root
          When running containers in Kubernetes, it's important to
          ensure that they  are properly secured to prevent       
          privilege escalation attacks.  One potential            
          vulnerability is when a container is allowed to run     
          applications as the root user, which could allow an     
          attacker to gain  access to sensitive resources. To     
          mitigate this risk, it's recommended to  add a          
          `securityContext` to the container, with the parameter  
          `runAsNonRoot`  set to `true`. This will ensure that the
          container runs as a non-root user,  limiting the damage 
          that could be caused by any potential attacks. By       
          adding a `securityContext` to the container in your     
          Kubernetes pod, you can  help to ensure that your       
          containerized applications are more secure and  less    
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/dgP5                            
                                                                  
           ▶▶┆ Autofix ▶ spec: securityContext: runAsNonRoot: true #
           15┆ spec:
   
    ❯❱ yaml.kubernetes.security.allow-privilege-escalation-no-      
       securitycontext.allow-privilege-escalation-no-securitycontext
          In Kubernetes, each pod runs in its own isolated        
          environment with its own set of security policies.      
          However, certain container images may contain `setuid`  
          or `setgid` binaries that could allow an attacker to    
          perform privilege escalation and gain access to         
          sensitive resources. To mitigate this risk, it's        
          recommended to add a `securityContext` to the container 
          in the pod, with the parameter                          
          `allowPrivilegeEscalation` set to `false`. This will    
          prevent the container from running any privileged       
          processes and limit the impact of any potential attacks.
          By adding a `securityContext` to your Kubernetes pod,   
          you can help to ensure that your containerized          
          applications are more secure and less vulnerable to     
          privilege escalation attacks.                           
          Details: https://sg.run/eleR                            
                                                                  
           ▶▶┆ Autofix ▶ securityContext: allowPrivilegeEscalation: false
              name                                                       
           18┆ - name: orders-service
                                                    
    k8s/apps/products-deployment.yaml
     ❱ yaml.kubernetes.security.run-as-non-root.run-as-non-root
          When running containers in Kubernetes, it's important to
          ensure that they  are properly secured to prevent       
          privilege escalation attacks.  One potential            
          vulnerability is when a container is allowed to run     
          applications as the root user, which could allow an     
          attacker to gain  access to sensitive resources. To     
          mitigate this risk, it's recommended to  add a          
          `securityContext` to the container, with the parameter  
          `runAsNonRoot`  set to `true`. This will ensure that the
          container runs as a non-root user,  limiting the damage 
          that could be caused by any potential attacks. By       
          adding a `securityContext` to the container in your     
          Kubernetes pod, you can  help to ensure that your       
          containerized applications are more secure and  less    
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/dgP5                            
                                                                  
           ▶▶┆ Autofix ▶ spec: securityContext: runAsNonRoot: true #
           15┆ spec:
   
    ❯❱ yaml.kubernetes.security.allow-privilege-escalation-no-      
       securitycontext.allow-privilege-escalation-no-securitycontext
          In Kubernetes, each pod runs in its own isolated        
          environment with its own set of security policies.      
          However, certain container images may contain `setuid`  
          or `setgid` binaries that could allow an attacker to    
          perform privilege escalation and gain access to         
          sensitive resources. To mitigate this risk, it's        
          recommended to add a `securityContext` to the container 
          in the pod, with the parameter                          
          `allowPrivilegeEscalation` set to `false`. This will    
          prevent the container from running any privileged       
          processes and limit the impact of any potential attacks.
          By adding a `securityContext` to your Kubernetes pod,   
          you can help to ensure that your containerized          
          applications are more secure and less vulnerable to     
          privilege escalation attacks.                           
          Details: https://sg.run/eleR                            
                                                                  
           ▶▶┆ Autofix ▶ securityContext: allowPrivilegeEscalation: false
              name                                                       
           18┆ - name: products-service
                                                 
    k8s/apps/users-deployment.yaml
     ❱ yaml.kubernetes.security.run-as-non-root.run-as-non-root
          When running containers in Kubernetes, it's important to
          ensure that they  are properly secured to prevent       
          privilege escalation attacks.  One potential            
          vulnerability is when a container is allowed to run     
          applications as the root user, which could allow an     
          attacker to gain  access to sensitive resources. To     
          mitigate this risk, it's recommended to  add a          
          `securityContext` to the container, with the parameter  
          `runAsNonRoot`  set to `true`. This will ensure that the
          container runs as a non-root user,  limiting the damage 
          that could be caused by any potential attacks. By       
          adding a `securityContext` to the container in your     
          Kubernetes pod, you can  help to ensure that your       
          containerized applications are more secure and  less    
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/dgP5                            
                                                                  
           ▶▶┆ Autofix ▶ spec: securityContext: runAsNonRoot: true #
           15┆ spec:
   
    ❯❱ yaml.kubernetes.security.allow-privilege-escalation-no-      
       securitycontext.allow-privilege-escalation-no-securitycontext
          In Kubernetes, each pod runs in its own isolated        
          environment with its own set of security policies.      
          However, certain container images may contain `setuid`  
          or `setgid` binaries that could allow an attacker to    
          perform privilege escalation and gain access to         
          sensitive resources. To mitigate this risk, it's        
          recommended to add a `securityContext` to the container 
          in the pod, with the parameter                          
          `allowPrivilegeEscalation` set to `false`. This will    
          prevent the container from running any privileged       
          processes and limit the impact of any potential attacks.
          By adding a `securityContext` to your Kubernetes pod,   
          you can help to ensure that your containerized          
          applications are more secure and less vulnerable to     
          privilege escalation attacks.                           
          Details: https://sg.run/eleR                            
                                                                  
           ▶▶┆ Autofix ▶ securityContext: allowPrivilegeEscalation: false
              name                                                       
           18┆ - name: users-service
                                           
    k8s/vault/manifests.yaml
     ❱ yaml.kubernetes.security.run-as-non-root.run-as-non-root
          When running containers in Kubernetes, it's important to
          ensure that they  are properly secured to prevent       
          privilege escalation attacks.  One potential            
          vulnerability is when a container is allowed to run     
          applications as the root user, which could allow an     
          attacker to gain  access to sensitive resources. To     
          mitigate this risk, it's recommended to  add a          
          `securityContext` to the container, with the parameter  
          `runAsNonRoot`  set to `true`. This will ensure that the
          container runs as a non-root user,  limiting the damage 
          that could be caused by any potential attacks. By       
          adding a `securityContext` to the container in your     
          Kubernetes pod, you can  help to ensure that your       
          containerized applications are more secure and  less    
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/dgP5                            
                                                                  
           ▶▶┆ Autofix ▶ spec: securityContext: runAsNonRoot: true #
           57┆ spec:
   
    ❯❱ yaml.kubernetes.security.allow-privilege-escalation.allow-
       privilege-escalation                                      
          In Kubernetes, each pod runs in its own isolated        
          environment with its own set of security policies.      
          However, certain container images may contain `setuid`  
          or `setgid` binaries that could allow an attacker to    
          perform privilege escalation and gain access to         
          sensitive resources. To mitigate this risk, it's        
          recommended to add a `securityContext` to the container 
          in the pod, with the parameter                          
          `allowPrivilegeEscalation` set to `false`. This will    
          prevent the container from running any privileged       
          processes and limit the impact of any potential attacks.
          By adding the `allowPrivilegeEscalation` parameter to   
          your the `securityContext`, you can help to ensure that 
          your containerized applications are more secure and less
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/ljp6                            
                                                                  
           ▶▶┆ Autofix ▶ securityContext: allowPrivilegeEscalation: false #
           84┆ securityContext:
   
     ❱ yaml.kubernetes.security.run-as-non-root.run-as-non-root
          When running containers in Kubernetes, it's important to
          ensure that they  are properly secured to prevent       
          privilege escalation attacks.  One potential            
          vulnerability is when a container is allowed to run     
          applications as the root user, which could allow an     
          attacker to gain  access to sensitive resources. To     
          mitigate this risk, it's recommended to  add a          
          `securityContext` to the container, with the parameter  
          `runAsNonRoot`  set to `true`. This will ensure that the
          container runs as a non-root user,  limiting the damage 
          that could be caused by any potential attacks. By       
          adding a `securityContext` to the container in your     
          Kubernetes pod, you can  help to ensure that your       
          containerized applications are more secure and  less    
          vulnerable to privilege escalation attacks.             
          Details: https://sg.run/dgP5                            
                                                                  
           ▶▶┆ Autofix ▶ spec: securityContext: runAsNonRoot: true #
          167┆ spec:
   
    ❯❱ yaml.kubernetes.security.allow-privilege-escalation-no-      
       securitycontext.allow-privilege-escalation-no-securitycontext
          In Kubernetes, each pod runs in its own isolated        
          environment with its own set of security policies.      
          However, certain container images may contain `setuid`  
          or `setgid` binaries that could allow an attacker to    
          perform privilege escalation and gain access to         
          sensitive resources. To mitigate this risk, it's        
          recommended to add a `securityContext` to the container 
          in the pod, with the parameter                          
          `allowPrivilegeEscalation` set to `false`. This will    
          prevent the container from running any privileged       
          processes and limit the impact of any potential attacks.
          By adding a `securityContext` to your Kubernetes pod,   
          you can help to ensure that your containerized          
          applications are more secure and less vulnerable to     
          privilege escalation attacks.                           
          Details: https://sg.run/eleR                            
                                                                  
           ▶▶┆ Autofix ▶ securityContext: allowPrivilegeEscalation: false
              name                                                       
          170┆ - name: vault-setup

                
                
┌──────────────┐
│ Scan Summary │
└──────────────┘
✅ CI scan completed successfully.
 • Findings: 31 (0 blocking)
 • Rules run: 130422
 • Targets scanned: 67
 • Parsed lines: ~99.8%
 • Scan skipped: 
   ◦ Files larger than  files 1.0 MB: 2
 • Scan was limited to files tracked by git
 • For a detailed list of skipped files and lines, run semgrep with the --verbose flag
CI scan completed successfully.
  View results in Semgrep Cloud Platform:
    https://semgrep.dev/orgs/abdelkader-benhmida-supcom-tn/findings?repo=local_scan/stage&ref=main
    https://semgrep.dev/orgs/abdelkader-benhmida-supcom-tn/supply-chain/vulnerabilities?repo=local_scan/stage&ref=main
  No blocking findings so exiting with code 0
host@hostname:~/Desktop/stage$ 
