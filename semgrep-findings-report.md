# Semgrep Security Scan — 31 Findings Detailed Report

**Tool:** Semgrep 1.168.0 Pro Engine
**Files scanned:** 67
**Status:** Non-blocking

---

## Category A: GitHub Actions Mutable Tags (18 findings)

**File:** `.github/workflows/ci-cd.yml`
**Rule:** `github-actions-mutable-action-tag`

**What this means:**
GitHub Actions steps reference third-party actions using mutable tags (like `@v4`) or branch names (like `@master`) instead of pinning to a specific commit SHA. Tags and branch names are not immutable — the action owner can silently move them to point to a different commit at any time.

**Why it's dangerous:**
If an attacker compromises the action repository (or the owner turns malicious), they can repoint the tag to a malicious commit. Your CI pipeline would then execute the malicious code without any change to your workflow file. This has already happened in real attacks (e.g., `trivy-action` and `kics-github-action` were compromised this way). The malicious code runs with your CI secrets, can modify your build artifacts, exfiltrate data, or inject backdoors.

**Fix:** Replace each tag with the full 40-character commit SHA:
```yaml
# Vulnerable
- uses: actions/checkout@v4

# Secure
- uses: actions/checkout@8ade135a41bc03ea155e62e844d188df1ea18608
```

---

### Finding 1
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 21
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed by the repo owner
- **Fix:** Pin to commit SHA, e.g. `uses: actions/checkout@8ade135a41bc03ea155e62e844d188df1ea18608`

### Finding 2
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 23
- **Current:** `uses: actions/setup-python@v5`
- **Problem:** Uses mutable tag `@v5` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 3
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 54
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 4
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 59
- **Current:** `uses: gitleaks/gitleaks-action@v2`
- **Problem:** Uses mutable tag `@v2` which can be repointed by gitleaks repo owner
- **Fix:** Pin to commit SHA

### Finding 5
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 75
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 6
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 77
- **Current:** `uses: actions/setup-python@v5`
- **Problem:** Uses mutable tag `@v5` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 7
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 116
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 8
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 119
- **Current:** `uses: docker/setup-buildx-action@v3`
- **Problem:** Uses mutable tag `@v3` which can be repointed by docker repo owner
- **Fix:** Pin to commit SHA

### Finding 9
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 122
- **Current:** `uses: docker/login-action@v3`
- **Problem:** Uses mutable tag `@v3` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 10
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 130
- **Current:** `uses: docker/metadata-action@v5`
- **Problem:** Uses mutable tag `@v5` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 11
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 139
- **Current:** `uses: docker/build-push-action@v5`
- **Problem:** Uses mutable tag `@v5` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 12
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 175
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 13
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 178
- **Current:** `uses: docker/login-action@v3`
- **Problem:** Uses mutable tag `@v3` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 14
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 191
- **Current:** `uses: aquasecurity/trivy-action@master`
- **Problem:** Uses mutable **branch** `@master` — most dangerous since branch HEAD changes on every commit. Trivy-action was previously compromised.
- **Fix:** Pin to commit SHA immediately

### Finding 15
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 201
- **Current:** `uses: aquasecurity/trivy-action@master`
- **Problem:** Uses mutable **branch** `@master` — same risk as finding 14
- **Fix:** Pin to commit SHA immediately

### Finding 16
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 209
- **Current:** `uses: actions/upload-artifact@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 17
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 222
- **Current:** `uses: actions/checkout@v4`
- **Problem:** Uses mutable tag `@v4` which can be repointed
- **Fix:** Pin to commit SHA

### Finding 18
- **File:** `.github/workflows/ci-cd.yml`
- **Line:** 224
- **Current:** `uses: hashicorp/setup-terraform@v3`
- **Problem:** Uses mutable tag `@v3` which can be repointed by hashicorp repo owner
- **Fix:** Pin to commit SHA

---

## Category B: Dockerfiles Missing USER Directive (3 findings)

**Rule:** `dockerfile.security.missing-user.missing-user`

**What this means:**
The Dockerfile does not specify a `USER` instruction. By default, Docker containers run as `root` (UID 0). Without a `USER` directive, the application inside the container executes with root privileges.

**Why it's dangerous:**
If an attacker exploits a vulnerability in your application (e.g., RCE via a deserialization flaw), they gain root access inside the container. From root, they can:
- Access all files in the container
- Attempt container escape exploits
- Access mounted secrets/volumes
- Pivot to other services on the network
- Install additional malware

Running as a non-root user limits the blast radius significantly — even if the app is compromised, the attacker only has the permissions of that user.

**Fix:** Add a non-root user before the `CMD` instruction:
```dockerfile
RUN adduser -D -s /bin/sh appuser
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```
Or use the simplified autofix:
```dockerfile
USER non-root
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### Finding 19
- **File:** `app/orders-service/Dockerfile`
- **Line:** 14
- **Problem:** No `USER` directive specified — container runs as `root`
- **Risk:** If attacker compromises the orders-service process, they get root inside the container → full control over the container, potential container escape, access to secrets
- **Fix:** Add `USER non-root` before the `CMD` instruction on line 14

### Finding 20
- **File:** `app/products-service/Dockerfile`
- **Line:** 12
- **Problem:** No `USER` directive specified — container runs as `root`
- **Risk:** If attacker compromises the products-service process, they get root inside the container → full control, access to secrets, potential escape to host
- **Fix:** Add `USER non-root` before the `CMD` instruction on line 12

### Finding 21
- **File:** `app/users-service/Dockerfile`
- **Line:** 12
- **Problem:** No `USER` directive specified — container runs as `root`
- **Risk:** If attacker compromises the users-service process, they get root inside the container → full control, access to user data and secrets
- **Fix:** Add `USER non-root` before the `CMD` instruction on line 12

---

## Category C: Kubernetes Missing `runAsNonRoot: true` (5 findings)

**Rule:** `yaml.kubernetes.security.run-as-non-root.run-as-non-root`

**What this means:**
The Kubernetes pod/container spec does not include a `securityContext` with `runAsNonRoot: true`. Without this setting, Kubernetes does not enforce that the container must run as a non-root user. Even if the Dockerfile sets a USER, this K8s setting acts as a double safety net — it will refuse to start the pod if the image tries to run as UID 0.

**Why it's dangerous:**
`runAsNonRoot: true` is a **runtime enforcement** — Kubernetes will block the container from starting if it attempts to run as root. Without it:
- A misconfigured or updated image that defaults to root will silently run as root
- There is no runtime guardrail preventing root execution
- Defense in depth is weakened — you rely solely on the Dockerfile's USER directive

**Risk chain:** Container runs as root → attacker exploits app vulnerability → gains root → privilege escalation → container escape → host compromise

**Fix:** Add `securityContext` at the pod spec level:
```yaml
spec:
  securityContext:
    runAsNonRoot: true
```

---

### Finding 22
- **File:** `k8s/apps/orders-deployment.yaml`
- **Line:** 15
- **Container:** orders-service
- **Problem:** Pod spec has no `securityContext.runAsNonRoot: true`
- **Risk:** No runtime enforcement preventing the container from running as root — if image defaults to root or is updated to root, it will silently run at UID 0
- **Fix:** Add `securityContext: runAsNonRoot: true` under the pod `spec`

### Finding 23
- **File:** `k8s/apps/products-deployment.yaml`
- **Line:** 15
- **Container:** products-service
- **Problem:** Pod spec has no `securityContext.runAsNonRoot: true`
- **Risk:** No runtime enforcement — container can run as root without being blocked
- **Fix:** Add `securityContext: runAsNonRoot: true` under the pod `spec`

### Finding 24
- **File:** `k8s/apps/users-deployment.yaml`
- **Line:** 15
- **Container:** users-service
- **Problem:** Pod spec has no `securityContext.runAsNonRoot: true`
- **Risk:** No runtime enforcement — container can run as root, putting user data at risk
- **Fix:** Add `securityContext: runAsNonRoot: true` under the pod `spec`

### Finding 25
- **File:** `k8s/vault/manifests.yaml`
- **Line:** 57
- **Container:** (vault container)
- **Problem:** Pod spec has no `securityContext.runAsNonRoot: true`
- **Risk:** Vault handles secrets — running as root means a compromise could expose all stored secrets and credentials
- **Fix:** Add `securityContext: runAsNonRoot: true` under the pod `spec` at line 57

### Finding 26
- **File:** `k8s/vault/manifests.yaml`
- **Line:** 167
- **Container:** vault-setup
- **Problem:** Pod spec has no `securityContext.runAsNonRoot: true`
- **Risk:** vault-setup container can run as root — if compromised, attacker can manipulate vault initialization and steal root tokens/keys
- **Fix:** Add `securityContext: runAsNonRoot: true` under the pod `spec` at line 167

---

## Category D: Kubernetes Missing `allowPrivilegeEscalation: false` (5 findings)

**Rule:** `yaml.kubernetes.security.allow-privilege-escalation-no-securitycontext` and `allow-privilege-escalation`

**What this means:**
The container spec does not include `securityContext.allowPrivilegeEscalation: false`. This Kubernetes setting controls whether a process can gain more privileges than its parent. When not set, it defaults to `true` (allowed) if the container is already running as root, or `false` if running as non-root — but you should set it explicitly to `false` to guarantee it.

**Why it's dangerous:**
Container images may contain `setuid` or `setgid` binaries — programs that run with elevated privileges regardless of who invokes them. Examples:
- `sudo`, `su`, `mount` binaries
- Custom binaries with SUID bit set

If `allowPrivilegeEscalation` is not explicitly set to `false`:
- A process can use `setuid` binaries to escalate from a low-privilege user to root
- An attacker who gets initial code execution can use these binaries to escalate
- Even if `runAsNonRoot` is set, an attacker could pivot to root via SUID binaries

Setting `allowPrivilegeEscalation: false` blocks this by preventing the kernel from granting more privileges than the parent process — even if SUID binaries exist in the image.

**Fix:** Add to the container's `securityContext`:
```yaml
containers:
  - name: my-service
    securityContext:
      allowPrivilegeEscalation: false
```

---

### Finding 27
- **File:** `k8s/apps/orders-deployment.yaml`
- **Line:** 18
- **Container:** orders-service
- **Problem:** Container has no `securityContext.allowPrivilegeEscalation: false`
- **Risk:** If image contains `setuid`/`setgid` binaries, an attacker can escalate from limited user to root inside the container
- **Fix:** Add `securityContext: allowPrivilegeEscalation: false` to the container spec

### Finding 28
- **File:** `k8s/apps/products-deployment.yaml`
- **Line:** 18
- **Container:** products-service
- **Problem:** Container has no `securityContext.allowPrivilegeEscalation: false`
- **Risk:** SUID binaries in image could enable privilege escalation — attacker escalates from app user to root
- **Fix:** Add `securityContext: allowPrivilegeEscalation: false` to the container spec

### Finding 29
- **File:** `k8s/apps/users-deployment.yaml`
- **Line:** 18
- **Container:** users-service
- **Problem:** Container has no `securityContext.allowPrivilegeEscalation: false`
- **Risk:** SUID binaries could allow attacker to escalate to root and access user credentials/data
- **Fix:** Add `securityContext: allowPrivilegeEscalation: false` to the container spec

### Finding 30
- **File:** `k8s/vault/manifests.yaml`
- **Line:** 84
- **Container:** (vault container)
- **Problem:** Container has `securityContext` but is missing `allowPrivilegeEscalation: false`
- **Risk:** Vault stores encryption keys and secrets — privilege escalation here means attacker could read/modify all stored secrets, unseal keys, and root tokens
- **Fix:** Add `allowPrivilegeEscalation: false` to the existing `securityContext` at line 84

### Finding 31
- **File:** `k8s/vault/manifests.yaml`
- **Line:** 170
- **Container:** vault-setup
- **Problem:** Container has no `securityContext.allowPrivilegeEscalation: false`
- **Risk:** vault-setup handles vault initialization — privilege escalation here means attacker can intercept root tokens during setup and gain permanent access to the vault
- **Fix:** Add `securityContext: allowPrivilegeEscalation: false` to the container spec at line 170

---

## Summary Table

| # | Category | Risk Level | Count |
|---|----------|------------|-------|
| 1-18 | GitHub Actions mutable tags | Medium-High (supply-chain) | 18 |
| 19-21 | Dockerfile missing USER | High (root execution) | 3 |
| 22-26 | K8s missing runAsNonRoot | High (no root guardrail) | 5 |
| 27-31 | K8s missing allowPrivilegeEscalation | High (privilege escalation) | 5 |
| | **Total** | | **31** |

## Recommended Priority

1. **Highest:** Findings 14, 15 (`trivy-action@master`) — branch reference, most exposed to supply-chain attack
2. **High:** Findings 1-13, 16-18 — pin all other GitHub Actions to SHA
3. **High:** Findings 22-31 — add K8s securityContext (defense in depth for production)
4. **Medium:** Findings 19-21 — add USER in Dockerfile (reduces blast radius)
