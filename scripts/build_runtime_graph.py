#!/usr/bin/env python3
"""Build focused Graphify runtime dependency and call graphs.

The default full-project graph intentionally mixes code, tests, documents, and
semantic relationships.  This script produces a separate, directed view that
only contains Python files under ``yuqing/`` and only structural AST edges.

Run with Graphify's interpreter, for example::

    "$(sed -n '1p' graphify-out/.graphify_python)" scripts/build_runtime_graph.py
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from graphify.analyze import god_nodes, suggest_questions, surprising_connections
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.diagnostics import diagnose_extraction
from graphify.export import to_html, to_json
from graphify.extract import extract
from graphify.report import generate


CALL_RELATIONS = {"calls", "indirect_call"}
IMPORT_RELATIONS = {"imports", "imports_from", "re_exports"}
RUNTIME_RELATIONS = CALL_RELATIONS | IMPORT_RELATIONS
EXCLUDED_RUNTIME_FILES = {
    "yuqing/architecture_check.py",
    "yuqing/selfcheck.py",
}


def _relative_source(value: str, root: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _community_labels(
    graph: nx.Graph,
    communities: dict[int, list[str]],
    suffix: str,
) -> dict[int, str]:
    labels: dict[int, str] = {}
    for community_id, members in communities.items():
        files = Counter(
            graph.nodes[node_id].get("source_file", "")
            for node_id in members
            if node_id in graph
        )
        source_file = files.most_common(1)[0][0] if files else "runtime"
        path = Path(source_file)
        stem = path.stem.replace("_", " ").title() or "Runtime"
        if "api" in path.parts:
            stem = f"API {stem}"
        labels[community_id] = f"{stem} {suffix}"[:60]
    return labels


def _filter_call_extraction(
    extraction: dict,
    root: Path,
    source_prefix: str,
) -> tuple[dict, dict[str, dict]]:
    code_nodes: dict[str, dict] = {}
    for raw_node in extraction.get("nodes", []):
        node = dict(raw_node)
        source_file = _relative_source(node.get("source_file", ""), root)
        node["source_file"] = source_file
        if (
            node.get("file_type") == "code"
            and source_file.startswith(source_prefix + "/")
            and source_file.endswith(".py")
        ):
            code_nodes[node["id"]] = node

    edges: list[dict] = []
    incident: set[str] = set()
    for raw_edge in extraction.get("edges", []):
        if raw_edge.get("relation") not in CALL_RELATIONS:
            continue
        source = raw_edge.get("source")
        target = raw_edge.get("target")
        if source not in code_nodes or target not in code_nodes or source == target:
            continue
        edge = dict(raw_edge)
        edge["source_file"] = _relative_source(edge.get("source_file", ""), root)
        edges.append(edge)
        incident.update((source, target))

    nodes = [code_nodes[node_id] for node_id in sorted(incident)]
    return {
        "nodes": nodes,
        "edges": edges,
        "hyperedges": [],
        "input_tokens": 0,
        "output_tokens": 0,
    }, code_nodes


def _build_module_graph(
    extraction: dict,
    code_nodes: dict[str, dict],
    root: Path,
    source_prefix: str,
    files: list[Path],
) -> nx.DiGraph:
    graph = nx.DiGraph()
    relative_files = [_relative_source(str(path), root) for path in files]
    for source_file in relative_files:
        module_name = source_file.removesuffix(".py").replace("/", ".")
        graph.add_node(
            source_file,
            label=module_name,
            file_type="code",
            source_file=source_file,
            source_location="L1",
        )

    aggregates: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for edge in extraction.get("edges", []):
        relation = edge.get("relation")
        if relation not in RUNTIME_RELATIONS:
            continue
        source_node = code_nodes.get(edge.get("source"))
        target_node = code_nodes.get(edge.get("target"))
        if not source_node or not target_node:
            continue
        source_file = source_node.get("source_file", "")
        target_file = target_node.get("source_file", "")
        if (
            source_file == target_file
            or not source_file.startswith(source_prefix + "/")
            or not target_file.startswith(source_prefix + "/")
        ):
            continue
        bucket = "calls" if relation in CALL_RELATIONS else "imports"
        aggregates[(source_file, target_file)][bucket] += 1

    for (source_file, target_file), counts in aggregates.items():
        call_count = counts["calls"]
        import_count = counts["imports"]
        relation = "calls" if call_count else "imports"
        graph.add_edge(
            source_file,
            target_file,
            relation=relation,
            confidence="EXTRACTED",
            confidence_score=1.0,
            source_file=source_file,
            source_location=None,
            context=f"{call_count} calls; {import_count} imports",
            call_count=call_count,
            import_count=import_count,
            weight=max(1, call_count + import_count),
        )
    return graph


def _strong_cycles(graph: nx.DiGraph, limit: int = 12) -> list[list[str]]:
    cycles = [
        sorted(component)
        for component in nx.strongly_connected_components(graph)
        if len(component) > 1
    ]
    cycles.sort(key=lambda component: (-len(component), component))
    return cycles[:limit]


def _top_nodes(graph: nx.DiGraph, *, outbound: bool, limit: int = 10) -> list[tuple[str, int]]:
    degree = graph.out_degree if outbound else graph.in_degree
    return sorted(degree, key=lambda item: (-item[1], str(item[0])))[:limit]


def _summary(
    files: list[Path],
    module_graph: nx.DiGraph,
    call_graph: nx.DiGraph,
    health: dict,
) -> str:
    module_cycles = _strong_cycles(module_graph)
    call_cycles = _strong_cycles(call_graph)

    def render_nodes(items: list[tuple[str, int]], graph: nx.DiGraph) -> str:
        return "\n".join(
            f"- `{graph.nodes[node_id].get('label', node_id)}`: {degree}"
            for node_id, degree in items
        ) or "- None"

    def render_cycles(cycles: list[list[str]], graph: nx.DiGraph) -> str:
        if not cycles:
            return "- None detected"
        return "\n".join(
            "- Strongly connected component: " + ", ".join(
                f"`{graph.nodes[node_id].get('label', node_id)}`" for node_id in cycle
            )
            for cycle in cycles
        )

    return f"""# Cyber-Intelligence Runtime Graph

This is a focused Graphify view of production Python code under `yuqing/`.
Tests, documents, OpenSpec files, static HTML prototypes, rationale nodes, and
semantic/inferred edges are intentionally excluded.

## Outputs

- `graph.html`: module-level directed runtime dependency graph.
- `callgraph.html`: function/class-level directed call graph.
- `graph.json`: module graph for `graphify query/path/explain --graph`.
- `callgraph.json`: detailed call graph for `graphify query/path/explain --graph`.

## Scope

- Python files: {len(files)}
- Module nodes: {module_graph.number_of_nodes()}
- Module edges: {module_graph.number_of_edges()}
- Detailed call nodes: {call_graph.number_of_nodes()}
- Detailed call edges: {call_graph.number_of_edges()}
- Edge confidence: structural AST extraction only (`EXTRACTED`)

## Highest outbound module dependencies

{render_nodes(_top_nodes(module_graph, outbound=True), module_graph)}

## Highest inbound module dependencies

{render_nodes(_top_nodes(module_graph, outbound=False), module_graph)}

## Module strongly connected components

{render_cycles(module_cycles, module_graph)}

## Detailed call cycles

{render_cycles(call_cycles, call_graph)}

## Graph health

- Missing endpoint edges: {health.get('missing_endpoint_edges', 0)}
- Dangling endpoint edges: {health.get('dangling_endpoint_edges', 0)}
- Self loops: {health.get('self_loop_edges', 0)}
- Directed same-endpoint collapses: {health.get('directed_same_endpoint_collapsed_edges', 0)}
"""


def build(source: Path, output: Path) -> None:
    root = Path.cwd().resolve()
    source = source.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Clean a legacy cache directory created by early versions of this script;
    # final runtime artifacts live directly under ``output``.
    shutil.rmtree(output / "graphify-out", ignore_errors=True)
    source_prefix = source.relative_to(root).as_posix()
    files = sorted(
        path for path in source.rglob("*.py")
        if (
            "__pycache__" not in path.parts
            and _relative_source(str(path), root) not in EXCLUDED_RUNTIME_FILES
        )
    )
    if not files:
        raise SystemExit(f"No Python files found under {source}")

    # Use the repository root as Graphify's cache root so AST source paths stay
    # repo-relative (``yuqing/foo.py``) and match the filtered node IDs.
    extraction = extract(files, cache_root=root)
    call_extraction, code_nodes = _filter_call_extraction(
        extraction, root, source_prefix
    )
    call_graph = build_from_json(call_extraction, root=str(root), directed=True)
    module_graph = _build_module_graph(
        extraction, code_nodes, root, source_prefix, files
    )
    if call_graph.number_of_nodes() == 0 or module_graph.number_of_nodes() == 0:
        raise SystemExit("Graphify runtime extraction produced an empty graph")

    call_communities = cluster(call_graph)
    module_communities = cluster(module_graph)
    call_labels = _community_labels(call_graph, call_communities, "Calls")
    module_labels = _community_labels(module_graph, module_communities, "Modules")

    to_json(
        module_graph,
        module_communities,
        str(output / "graph.json"),
        force=True,
        community_labels=module_labels,
    )
    to_json(
        call_graph,
        call_communities,
        str(output / "callgraph.json"),
        force=True,
        community_labels=call_labels,
    )
    to_html(
        module_graph,
        module_communities,
        str(output / "graph.html"),
        community_labels=module_labels,
    )
    to_html(
        call_graph,
        call_communities,
        str(output / "callgraph.html"),
        community_labels=call_labels,
    )

    total_words = sum(
        len(path.read_text(encoding="utf-8", errors="ignore").split())
        for path in files
    )
    detection = {
        "total_files": len(files),
        "total_words": total_words,
        "files": {"code": [str(path) for path in files]},
        "skipped_sensitive": [],
        "warning": None,
    }
    module_cohesion = score_all(module_graph, module_communities)
    module_gods = god_nodes(module_graph)
    module_surprises = surprising_connections(module_graph, module_communities)
    module_questions = suggest_questions(
        module_graph, module_communities, module_labels
    )
    report = generate(
        module_graph,
        module_communities,
        module_cohesion,
        module_labels,
        module_gods,
        module_surprises,
        detection,
        {"input": 0, "output": 0},
        source_prefix,
        suggested_questions=module_questions,
    )
    (output / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")

    health = diagnose_extraction(call_extraction, directed=True, root=str(root))
    (output / "health.json").write_text(
        json.dumps(health, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output / "SUMMARY.md").write_text(
        _summary(files, module_graph, call_graph, health), encoding="utf-8"
    )
    (output / ".graphify_python").write_text(
        str(Path(__import__("sys").executable)), encoding="utf-8"
    )
    print(
        "Runtime graphs written: "
        f"{module_graph.number_of_nodes()} modules/{module_graph.number_of_edges()} edges; "
        f"{call_graph.number_of_nodes()} call nodes/{call_graph.number_of_edges()} edges"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="yuqing", help="Runtime source directory")
    parser.add_argument(
        "--output", default="graphify-runtime", help="Output directory"
    )
    args = parser.parse_args()
    build(Path(args.source), Path(args.output))


if __name__ == "__main__":
    main()
