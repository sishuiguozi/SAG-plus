// SAG bundle 优化：three-render-objects（3d-force-graph 渲染层）静态引入 `three/webgpu`，
// 但本应用 3D 图谱只走 WebGL 渲染器，WebGPURenderer 在运行时从不实例化。
// 通过 webpack alias 把 `three/webgpu` 指向本 stub，消除 ~2.1MB 的 three.webgpu.js 打包副本。
export const WebGPURenderer = undefined;
