#!/usr/bin/env python3
"""
Analyze all K8s manifests and build a REAL dependency graph
with semantic relationships (not just flat containment).
"""
import os
import sys
import yaml
from collections import defaultdict

def load_manifests(root_dir):
    """Load all YAML documents from the directory tree."""
    manifests = []
    for dirpath, _, fnames in os.walk(root_dir):
        for f in fnames:
            if not (f.endswith('.yaml') or f.endswith('.yml')):
                continue
            fpath = os.path.join(dirpath, f)
            try:
                with open(fpath) as fh:
                    for doc in yaml.safe_load_all(fh):
                        if doc and 'kind' in doc and 'metadata' in doc:
                            manifests.append((fpath, doc))
            except Exception as e:
                print(f"  [WARN] {fpath}: {e}", file=sys.stderr)
    return manifests

def extract_relationships(manifests):
    """Extract semantic relationships between resources."""
    nodes = {}       # (kind, namespace, name) -> label
    edges = []       # (from_kind, from_ns, from_name, to_kind, to_ns, to_name, label)

    # Pass 1: index all resources
    for fpath, doc in manifests:
        kind = doc['kind']
        meta = doc.get('metadata', {})
        name = meta.get('name', 'unknown')
        ns = meta.get('namespace', 'default')
        # Skip kustomize config
        if kind == 'Kustomization':
            continue
        nodes[(kind, ns, name)] = f"{kind}/{name}"

    # Pass 2: extract relationships
    for fpath, doc in manifests:
        kind = doc['kind']
        meta = doc.get('metadata', {})
        name = meta.get('name', 'unknown')
        ns = meta.get('namespace', 'default')
        spec = doc.get('spec', {})
        
        if kind == 'HorizontalPodAutoscaler':
            # HPA → Deployment (scaleTargetRef)
            ref = spec.get('scaleTargetRef', {})
            if ref:
                edges.append((
                    kind, ns, name,
                    ref.get('kind', 'Deployment'), ns, ref.get('name', ''),
                    'scaleTargetRef'
                ))

        elif kind == 'Deployment':
            # Deployment → ServiceAccount (serviceAccountName)
            tmpl_spec = spec.get('template', {}).get('spec', {})
            sa = tmpl_spec.get('serviceAccountName', '')
            if sa:
                edges.append((
                    kind, ns, name,
                    'ServiceAccount', ns, sa,
                    'serviceAccountName'
                ))
            # Deployment → Secret (env.valueFrom.secretKeyRef)
            for c in tmpl_spec.get('containers', []):
                for env in c.get('env', []):
                    sref = env.get('valueFrom', {}).get('secretKeyRef', {})
                    if sref:
                        secret_ns = ns  # same namespace
                        edges.append((
                            kind, ns, name,
                            'Secret', secret_ns, sref.get('name', ''),
                            'secretKeyRef'
                        ))
                for envf in c.get('envFrom', []):
                    cmref = envf.get('configMapRef', {})
                    if cmref:
                        edges.append((
                            kind, ns, name,
                            'ConfigMap', ns, cmref.get('name', ''),
                            'configMapRef'
                        ))
                    sref = envf.get('secretRef', {})
                    if sref:
                        edges.append((
                            kind, ns, name,
                            'Secret', ns, sref.get('name', ''),
                            'secretRef'
                        ))
            # Deployment → ConfigMap (volumes.configMap)
            for vol in tmpl_spec.get('volumes', []):
                cm = vol.get('configMap', {})
                if cm:
                    edges.append((
                        kind, ns, name,
                        'ConfigMap', ns, cm.get('name', ''),
                        'configMap-volume'
                    ))
                sec = vol.get('secret', {})
                if sec:
                    edges.append((
                        kind, ns, name,
                        'Secret', ns, sec.get('secretName', ''),
                        'secret-volume'
                    ))

        elif kind == 'Service':
            # Service → Deployment (via selector matching name)
            sel = spec.get('selector', {})
            for key, val in sel.items():
                if key in ('app', 'app.kubernetes.io/name'):
                    edges.append((
                        kind, ns, name,
                        'Deployment', ns, val,
                        f'selector({key}={val})'
                    ))

        elif kind == 'RoleBinding':
            # RoleBinding → ServiceAccount (subjects)
            for sub in spec.get('subjects', []):
                sub_ns = sub.get('namespace', ns)
                edges.append((
                    kind, ns, name,
                    sub.get('kind', 'ServiceAccount'), sub_ns, sub.get('name', ''),
                    'subjects'
                ))
            # RoleBinding → Role (roleRef)
            ref = spec.get('roleRef', {})
            if ref:
                edges.append((
                    kind, ns, name,
                    ref.get('kind', 'Role'), ns, ref.get('name', ''),
                    'roleRef'
                ))

        elif kind == 'PodDisruptionBudget':
            # PDB → Pod/Deployment (selector matchLabels-based)
            sel = spec.get('selector', {})
            for key, val in sel.get('matchLabels', {}).items():
                if key in ('app', 'app.kubernetes.io/name'):
                    edges.append((
                        kind, ns, name,
                        'Deployment', ns, val,
                        'protects'
                    ))

        elif kind == 'Application':  # ArgoCD
            # ArgoCD Application → targets a namespace
            dest = spec.get('destination', {})
            dest_ns = dest.get('namespace', 'default')
            edges.append((
                kind, ns, name,
                'Namespace', dest_ns, dest_ns,
                'destination'
            ))

        elif kind == 'ServiceMonitor':
            # ServiceMonitor → Service (via selector + namespaceSelector)
            sel = spec.get('selector', {})
            ns_sel = spec.get('namespaceSelector', {})
            target_ns = ns
            if ns_sel.get('matchNames'):
                target_ns = ns_sel['matchNames'][0]
            for key, val in sel.get('matchLabels', {}).items():
                if key in ('app', 'app.kubernetes.io/name'):
                    edges.append((
                        kind, ns, name,
                        'Service', target_ns, val,
                        'scrapes'
                    ))

        elif kind == 'Job':
            # Job → ConfigMap (volumes.configMap)
            tmpl_spec = spec.get('template', {}).get('spec', {})
            for vol in tmpl_spec.get('volumes', []):
                cm = vol.get('configMap', {})
                if cm:
                    edges.append((
                        kind, ns, name,
                        'ConfigMap', ns, cm.get('name', ''),
                        'configMap-volume'
                    ))
            # Job → Secret (env.valueFrom.secretKeyRef)
            for c in tmpl_spec.get('containers', []):
                for env in c.get('env', []):
                    sref = env.get('valueFrom', {}).get('secretKeyRef', {})
                    if sref:
                        secret_ns = ns
                        edges.append((
                            kind, ns, name,
                            'Secret', secret_ns, sref.get('name', ''),
                            'secretKeyRef'
                        ))

        elif kind == 'NetworkPolicy':
            # NetworkPolicy → Namespace (egress/ingress to other namespaces)
            for ptype in spec.get('policyTypes', []):
                if ptype == 'Egress':
                    for rule in spec.get('egress', []):
                        for to in rule.get('to', []):
                            nssel = to.get('namespaceSelector', {})
                            ml = nssel.get('matchLabels', {})
                            if isinstance(ml, dict) and 'kubernetes.io/metadata.name' in ml:
                                target_ns = ml['kubernetes.io/metadata.name']
                                edges.append((
                                    kind, ns, name,
                                    'Namespace', target_ns, target_ns,
                                    'egress-to'
                                ))
                if ptype == 'Ingress':
                    for rule in spec.get('ingress', []):
                        for frm in rule.get('from', []):
                            nssel = frm.get('namespaceSelector', {})
                            ml = nssel.get('matchLabels', {})
                            if isinstance(ml, dict) and 'kubernetes.io/metadata.name' in ml:
                                target_ns = ml['kubernetes.io/metadata.name']
                                edges.append((
                                    kind, ns, name,
                                    'Namespace', target_ns, target_ns,
                                    'ingress-from'
                                ))

    return nodes, edges

def render_mermaid_enhanced(nodes, edges):
    """Render a clean Mermaid diagram with namespace subgraphs."""
    # Group nodes by namespace
    by_ns = defaultdict(list)
    for (kind, ns, name) in nodes:
        by_ns[ns].append((kind, ns, name))
    
    ns_display_names = {
        'devops-platform': 'Apps (devops-platform)',
        'vault': 'Vault',
        'monitoring': 'Monitoring',
        'argocd': 'ArgoCD (GitOps)',
        'kube-system': 'K8s System',
        'default': 'Default',
    }
    
    lines = ["flowchart LR"]
    lines.append("  %% Nodes")
    
    # Allocate IDs
    node_id = {}
    for (kind, ns, name) in nodes:
        key = (kind, ns, name)
        id_str = f"N{len(node_id)}"
        node_id[key] = id_str
    
    # Render namespace subgraphs
    for ns, group in by_ns.items():
        if not group:
            continue
        display = ns_display_names.get(ns, ns)
        lines.append(f"  subgraph {display.replace(' ', '_')}[\"{display}\"]")
        for (kind, ns, name) in sorted(group, key=lambda x: (x[0], x[2])):
            kid = node_id[(kind, ns, name)]
            # Emoji for resource types
            emoji = {
                'Deployment': '⚙️',
                'Service': '🌐',
                'HorizontalPodAutoscaler': '📈',
                'ServiceAccount': '🆔',
                'Role': '🔑',
                'RoleBinding': '🔗',
                'PodDisruptionBudget': '🛡️',
                'NetworkPolicy': '🚦',
                'ConfigMap': '📋',
                'Secret': '🔒',
                'Job': '▶️',
                'Namespace': '📦',
                'ServiceMonitor': '📊',
                'Application': '🔄',
            }.get(kind, '📄')
            label_parts = name.split('/')
            short_name = label_parts[-1] if len(label_parts) > 1 else name
            lines.append(f"    {kid}[\"{emoji} {kind}: {short_name}\"]")
        lines.append("  end")
    
    # Add edges
    lines.append("")
    lines.append("  %% Relationships")
    for (fk, fns, fn, tk, tns, tn, label) in edges:
        fkey = (fk, fns, fn)
        tkey = (tk, tns, tn)
        if fkey in node_id and tkey in node_id:
            fid = node_id[fkey]
            tid = node_id[tkey]
            style = " ==> "
            lines.append(f"    {fid}{style}|{label}|{tid}")
        # else: target not found, skip
    return "\n".join(lines)

if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else 'k8s/'
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', root)
    print(f"Scanning: {os.path.abspath(path)}", file=sys.stderr)
    manifests = load_manifests(path)
    print(f"Found {len(manifests)} resources", file=sys.stderr)
    nodes, edges = extract_relationships(manifests)
    print(f"Nodes: {len(nodes)}, Edges: {len(edges)}", file=sys.stderr)
    
    # Print relationship summary
    edge_types = defaultdict(int)
    for e in edges:
        edge_types[e[6]] += 1
    print("\nRelationship types:", file=sys.stderr)
    for et, count in sorted(edge_types.items(), key=lambda x: -x[1]):
        print(f"  {et}: {count}", file=sys.stderr)
    
    print(render_mermaid_enhanced(nodes, edges))