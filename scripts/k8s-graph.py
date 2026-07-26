#!/usr/bin/env python3
"""Analyze Kubernetes manifests and generate a dependency graph (Mermaid/DOT)."""
import sys
import os
import yaml
import re
from collections import defaultdict

def parse_yaml_files(root_dir):
    """Parse all YAML files in the directory tree and extract resources."""
    resources = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if not (fname.endswith('.yaml') or fname.endswith('.yml')):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath) as f:
                    for doc in yaml.safe_load_all(f):
                        if doc and 'kind' in doc and 'metadata' in doc:
                            resources.append((fpath, doc))
            except Exception as e:
                print(f"Warning: {fpath}: {e}", file=sys.stderr)
    return resources

def extract_name(metadata):
    """Extract resource name, handling namespaced names."""
    ns = metadata.get('namespace', 'default')
    name = metadata.get('name', 'unknown')
    return f"{ns}/{name}"

def find_refs(doc):
    """Find references to other K8s resources in a manifest."""
    refs = []
    kind = doc['kind']
    metadata = doc.get('metadata', {})
    name = extract_name(metadata)
    
    if kind == 'Deployment':
        # References ServiceAccount
        tmpl = doc.get('spec', {}).get('template', {})
        sa = tmpl.get('spec', {}).get('serviceAccountName', '')
        if sa:
            refs.append(('ServiceAccount', sa))
        # References ConfigMaps and Secrets via volumes
        volumes = tmpl.get('spec', {}).get('volumes', [])
        for vol in volumes:
            if 'configMap' in vol:
                cm_name = vol['configMap']['name']
                refs.append(('ConfigMap', cm_name, 'volumes'))
            if 'secret' in vol:
                s_name = vol['secret']['secretName']
                refs.append(('Secret', s_name, 'volumes'))
        # References ConfigMaps via envFrom
        containers = tmpl.get('spec', {}).get('containers', [])
        for c in containers:
            for envf in c.get('envFrom', []):
                if 'configMapRef' in envf:
                    refs.append(('ConfigMap', envf['configMapRef']['name'], 'envFrom'))
                if 'secretRef' in envf:
                    refs.append(('Secret', envf['secretRef']['name'], 'envFrom'))
        # References HPA
        refs.append(('HorizontalPodAutoscaler', name.split('/')[-1], 'scales'))

    elif kind == 'Service':
        # References the app via selector
        sel = doc.get('spec', {}).get('selector', {})
        if sel:
            app = sel.get('app', '')
            if app:
                refs.append(('Pod/Deployment', app, 'selector'))

    elif kind == 'HorizontalPodAutoscaler':
        # References the target Deployment
        scale_ref = doc.get('spec', {}).get('scaleTargetRef', {})
        if scale_ref:
            refs.append((scale_ref.get('kind', 'Deployment'), scale_ref.get('name', ''), 'scales'))

    elif kind == 'ServiceAccount':
        # References secrets
        for s in doc.get('secrets', []):
            refs.append(('Secret', s.get('name', ''), 'mounted'))

    elif kind == 'ConfigMap':
        pass  # usually referenced by Deployments

    # Generic: look for namespace references
    ns = metadata.get('namespace', '')
    if ns and ns != 'default':
        refs.append(('Namespace', ns, 'in'))

    return refs

def build_graph(resources):
    """Build adjacency list of resource dependencies."""
    nodes = {}  # (kind, name) -> display_label
    edges = []  # (from_node, to_node, label)
    
    for fpath, doc in resources:
        kind = doc['kind']
        meta = doc.get('metadata', {})
        name = meta.get('name', 'unknown')
        ns = meta.get('namespace', 'default')
        node_key = (kind, f"{ns}/{name}")
        nodes[node_key] = f"{kind}/{name}"
        
        refs = find_refs(doc)
        for ref in refs:
            ref_kind, ref_name = ref[0], ref[1]
            ref_ns = meta.get('namespace', 'default')
            ref_key = (ref_kind, f"{ref_ns}/{ref_name}")
            if ref_key not in nodes:
                # Create placeholder node
                label = ref[1] if '/' in ref[1] else f"{ref_kind}/{ref_name}"
                nodes[ref_key] = label
            edge_label = ref[2] if len(ref) > 2 else ''
            edges.append((node_key, ref_key, edge_label))
    
    return nodes, edges

def render_mermaid(nodes, edges):
    """Render the graph as Mermaid diagram."""
    lines = ["flowchart RL"]
    
    # Group by namespace for subgraphs
    ns_groups = defaultdict(list)
    for node_key in nodes:
        kind, name = node_key
        ns = name.split('/')[0]
        ns_groups[ns].append(node_key)
    
    for ns, group in ns_groups.items():
        lines.append(f"  subgraph {ns}")
        for node_key in group:
            kind, name = node_key
            short_name = name.split('/')[-1]
            node_id = f"{kind}_{short_name}".replace('-', '_').replace('.', '_')
            label = f"{kind}: {short_name}"
            lines.append(f"    {node_id}[\"{label}\"]")
        lines.append("  end")
    
    # Add edges
    for from_key, to_key, label in edges:
        f_kind, f_name = from_key
        f_short = f_name.split('/')[-1]
        t_kind, t_name = to_key
        t_short = t_name.split('/')[-1]
        
        from_id = f"{f_kind}_{f_short}".replace('-', '_').replace('.', '_')
        to_id = f"{t_kind}_{t_short}".replace('-', '_').replace('.', '_')
        
        edge_text = f" --> {to_id}"
        if label:
            edge_text = f" -- {label} --> {to_id}"
        lines.append(f"    {from_id}{edge_text}")
    
    return "\n".join(lines)

if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else 'k8s/'
    resources = parse_yaml_files(root)
    print(f"Found {len(resources)} resources", file=sys.stderr)
    nodes, edges = build_graph(resources)
    print(f"Nodes: {len(nodes)}, Edges: {len(edges)}", file=sys.stderr)
    print(render_mermaid(nodes, edges))