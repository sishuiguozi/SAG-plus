import { describe, expect, it } from "vitest";

import {
  orbitalGraphPerformanceProfile,
  sourceGraph2dPerformanceProfile,
} from "./graph-performance";

describe("sourceGraph2dPerformanceProfile", () => {
  it("uses the deterministic radial fallback for a dense first view", () => {
    expect(sourceGraph2dPerformanceProfile(227)).toEqual({
      useForceLayout: false,
      simulationTicks: 0,
      collisionIterations: 1,
    });
  });

  it("retains force layout with a bounded simulation for smaller graphs", () => {
    expect(sourceGraph2dPerformanceProfile(120)).toMatchObject({
      useForceLayout: true,
      simulationTicks: 200,
      collisionIterations: 2,
    });
  });
});

describe("orbitalGraphPerformanceProfile", () => {
  it("reduces geometry, pixel ratio, and frame rate for the AFSIM-sized view", () => {
    expect(
      orbitalGraphPerformanceProfile({
        eventCount: 220,
        entityCount: 7,
        relationCount: 257,
      }),
    ).toEqual({
      antialias: false,
      pixelRatioCap: 1.25,
      plateGeometryDetail: 3,
      edgeCurveSegments: 14,
      minimumFrameIntervalMs: 1000 / 30,
      backgroundPointCount: 160,
    });
  });

  it("keeps full quality for a small graph", () => {
    expect(
      orbitalGraphPerformanceProfile({
        eventCount: 20,
        entityCount: 10,
        relationCount: 30,
      }),
    ).toMatchObject({
      antialias: true,
      pixelRatioCap: 2,
      plateGeometryDetail: 4,
      edgeCurveSegments: 26,
      minimumFrameIntervalMs: 0,
    });
  });
});
