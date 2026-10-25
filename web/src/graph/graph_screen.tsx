import ForceGraph2D from "react-force-graph-2d";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTableRows } from "../state/hooks";
import { build_graph, type GraphNode } from "./build_graph";

function node_color(node: GraphNode) {
    if (node.unknown) return "gray";
    if (node.risk > 0.5) return "red";
    if (node.risk >= 0.05) return "orange";
    return "seagreen";
}

/** The size of an element, kept up to date as the window resizes, so the canvas always fills its container. */
function useElementSize() {
    const ref = useRef<HTMLDivElement>(null);
    const [size, setSize] = useState({ width: 0, height: 0 });

    useEffect(() => {
        const element = ref.current;
        if (!element) return;
        const observer = new ResizeObserver(([entry]) => {
            const { width, height } = entry.contentRect;
            setSize({ width: Math.floor(width), height: Math.floor(height) });
        });
        observer.observe(element);
        return () => observer.disconnect();
    }, []);

    return { ref, ...size };
}

export function GraphScreen() {
    const states = useTableRows("entity_state");
    const interactions = useTableRows("interaction");
    const { ref, width, height } = useElementSize();

    // second and would otherwise rebuild the graph each time.
    const shown_versions = states.filter((state) => state.version === 0 || state.caused_by !== null).length;
    const graph_key = `${shown_versions}:${interactions.length}`;

    // Node objects from the last build, by id. ForceGraph2D stores each node's position on the object itself,
    // so handing back the same object keeps the node where it was.
    const previous_nodes = useRef(new Map<string, GraphNode>());

    const data = useMemo(() => {
        const graph = build_graph(states, interactions);
        graph.nodes = graph.nodes.map((node) => previous_nodes.current.get(node.id) ?? node);
        previous_nodes.current = new Map(graph.nodes.map((node) => [node.id, node]));
        return graph;
    }, [graph_key]);

    return (
        <div ref={ref} className="graph_container">
            {width > 0 && (
                <ForceGraph2D
                    graphData={data}
                    width={width}
                    height={height}
                    nodeId="id"
                    nodeLabel={(node) => `${node.entity_id} v${node.version} (t=${node.t_ms} ms)`}
                    nodeColor={node_color}
                    nodeRelSize={5}
                    linkColor={(link) => (link.kind === "cause" ? "crimson" : "#bbb")}
                    linkWidth={(link) => (link.kind === "cause" ? 2 : 1)}
                    linkDirectionalArrowLength={4}
                    linkDirectionalArrowRelPos={1}
                    linkLabel={(link) => link.action ?? ""}
                />
            )}
        </div>
    );

}
