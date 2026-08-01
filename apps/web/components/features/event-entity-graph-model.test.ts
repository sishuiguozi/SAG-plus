import { describe, expect, it } from "vitest";

import {
  clusterEventEntityGraph,
  entityClusterKeyFromId,
  sliceEventEntityGraph,
} from "./event-entity-graph-model";
import type { SourceGraphResponse } from "../../lib/types";

function graphFixture(): SourceGraphResponse {
  return {
    documents: [],
    events: [
      {
        id: "event-linked",
        document_id: null,
        title: "关联事件",
        summary: "",
        category: "",
        rank: 0,
        parent_id: null,
        chunk_id: null,
        start_time: null,
      },
      {
        id: "event-isolated",
        document_id: null,
        title: "孤立事件",
        summary: "",
        category: "",
        rank: 0,
        parent_id: null,
        chunk_id: null,
        start_time: null,
      },
    ],
    entities: [
      { id: "entity-linked", name: "关联实体", type: "concept", description: "", heat: 1 },
      { id: "entity-isolated", name: "孤立实体", type: "concept", description: "", heat: 0 },
    ],
    relations: [
      {
        source_id: "entity-linked",
        source_kind: "entity",
        target_id: "event-linked",
        target_kind: "event",
        kind: "mentions",
        weight: 1,
        description: "",
      },
      {
        source_id: "event-linked",
        source_kind: "event",
        target_id: "entity-linked",
        target_kind: "entity",
        kind: "mentions",
        weight: 1,
        description: "重复方向",
      },
    ],
    counts: {
      documents: 0,
      events: 2,
      entities: 2,
      shown_documents: 0,
      shown_events: 2,
      shown_entities: 2,
      shown_relations: 2,
    },
    truncated: false,
  };
}

describe("sliceEventEntityGraph", () => {
  it("normalizes relation direction, deduplicates edges, and preserves all graph nodes", () => {
    const slice = sliceEventEntityGraph(graphFixture());

    expect(slice.relations).toEqual([
      {
        id: "mention:event-linked:entity-linked",
        eventId: "event-linked",
        entityId: "entity-linked",
      },
    ]);
    expect(slice.events.map((event) => event.id)).toEqual(["event-linked", "event-isolated"]);
    expect(slice.entities.map((entity) => entity.id)).toEqual([
      "entity-linked",
      "entity-isolated",
    ]);
  });

  it("preserves every returned event when only a few events have entity relations", () => {
    const graph = graphFixture();
    graph.events = Array.from({ length: 73 }, (_, index) => ({
      ...graph.events[index === 0 ? 0 : 1],
      id: `event-${index + 1}`,
      title: `事件 ${index + 1}`,
    }));
    graph.relations = [
      {
        source_id: "event-1",
        source_kind: "event",
        target_id: "entity-linked",
        target_kind: "entity",
        kind: "mentions",
        weight: 1,
        description: "",
      },
      {
        source_id: "event-2",
        source_kind: "event",
        target_id: "entity-linked",
        target_kind: "entity",
        kind: "mentions",
        weight: 1,
        description: "",
      },
      {
        source_id: "event-3",
        source_kind: "event",
        target_id: "entity-isolated",
        target_kind: "entity",
        kind: "mentions",
        weight: 1,
        description: "",
      },
    ];
    graph.counts.events = 73;
    graph.counts.shown_events = 73;

    const slice = sliceEventEntityGraph(graph);

    expect(slice.events).toHaveLength(73);
    expect(slice.events.map((event) => event.id)).toEqual(
      Array.from({ length: 73 }, (_, index) => `event-${index + 1}`),
    );
    expect(slice.relations).toHaveLength(3);
  });
});

describe("clusterEventEntityGraph", () => {
  it("collapses dense entity types and deduplicates event-to-cluster edges", () => {
    const graph = graphFixture();
    graph.entities = Array.from({ length: 12 }, (_, index) => ({
      id: `entity-${index}`,
      name: `实体 ${index}`,
      type: index < 10 ? "class" : "module",
      description: "",
      heat: 12 - index,
    }));
    graph.relations = graph.entities.map((entity) => ({
      source_id: "event-linked",
      source_kind: "event" as const,
      target_id: entity.id,
      target_kind: "entity" as const,
      kind: "mentions" as const,
      weight: 1,
      description: "",
    }));

    const clustered = clusterEventEntityGraph(
      sliceEventEntityGraph(graph),
      new Set(),
      { minimumEntityCount: 1, minimumClusterSize: 4 },
    );

    expect(clustered.slice.entities).toHaveLength(3);
    expect(clustered.slice.relations).toHaveLength(3);
    expect(clustered.clusteredEntityCount).toBe(10);
    const cluster = [...clustered.clusters.values()][0];
    expect(cluster.label).toBe("class");
    expect(cluster.members).toHaveLength(10);
    expect(entityClusterKeyFromId(cluster.id)).toBe("class");
  });

  it("restores real entities and relations when a cluster is expanded", () => {
    const graph = graphFixture();
    graph.entities = Array.from({ length: 6 }, (_, index) => ({
      id: `entity-${index}`,
      name: `实体 ${index}`,
      type: "class",
      description: "",
      heat: 1,
    }));
    graph.relations = graph.entities.map((entity) => ({
      source_id: "event-linked",
      source_kind: "event" as const,
      target_id: entity.id,
      target_kind: "entity" as const,
      kind: "mentions" as const,
      weight: 1,
      description: "",
    }));

    const clustered = clusterEventEntityGraph(
      sliceEventEntityGraph(graph),
      new Set(["class"]),
      { minimumEntityCount: 1, minimumClusterSize: 4 },
    );

    expect(clustered.clusters.size).toBe(0);
    expect(clustered.slice.entities).toHaveLength(6);
    expect(clustered.slice.relations).toHaveLength(6);
  });
});
