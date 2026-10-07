import * as THREE from "three";
import { axisFraction, densityColor, palettes } from "./graph";
import type { GraphOptions, ThreeDData, ThreeDView } from "./types";
import { canvasGraphText } from "./graphTypography";

export const defaultCamera = {
  yaw: -0.65,
  pitch: 0.45,
  zoom: 1,
  pan: [0, 0] as [number, number],
};
export function project3D(point: number[], view: ThreeDView, aspect = 1) {
  const [a, b, c] = point.map((v) => v * 2 - 1);
  const yaw = view.yaw ?? defaultCamera.yaw,
    pitch = view.pitch ?? defaultCamera.pitch;
  const x = a * Math.cos(yaw) + c * Math.sin(yaw),
    z = -a * Math.sin(yaw) + c * Math.cos(yaw);
  const y = b * Math.cos(pitch) - z * Math.sin(pitch),
    depth = b * Math.sin(pitch) + z * Math.cos(pitch);
  return [
    ((x - (view.pan?.[0] ?? 0)) * (view.zoom ?? 1)) / (2 * aspect),
    ((y - (view.pan?.[1] ?? 0)) * (view.zoom ?? 1)) / 2,
    depth,
  ];
}
const cubeCorners = (b = [0, 1, 0, 1, 0, 1]) =>
  Array.from({ length: 8 }, (_, i) => [
    b[i & 1 ? 1 : 0],
    b[i & 2 ? 3 : 2],
    b[i & 4 ? 5 : 4],
  ]);
const cubeEdges = Array.from({ length: 8 }, (_, i) =>
  [1, 2, 4].filter((v) => !(i & v)).map((v) => [i, i | v]),
).flat();
const vertexShader = `
attribute float scalarColor;
attribute float scalarSize;
attribute float backgate;
uniform float yaw;
uniform float pitch;
uniform float zoom;
uniform vec2 pan;
uniform float aspect;
uniform float pointSize;
uniform float pixelRatio;
uniform float useSize;
varying float value;
varying float highlight;
void main() {
 vec3 p=position*2.0-1.0;
 float x=p.x*cos(yaw)+p.z*sin(yaw);
 float z=-p.x*sin(yaw)+p.z*cos(yaw);
 float y=p.y*cos(pitch)-z*sin(pitch);
 float depth=p.y*sin(pitch)+z*cos(pitch);
 gl_Position=vec4((x-pan.x)*zoom/(2.0*aspect),(y-pan.y)*zoom/2.0,-depth*0.1,1.0);
 gl_PointSize=pointSize*pixelRatio*mix(1.0,0.4+scalarSize*1.6,useSize);
 value=scalarColor; highlight=backgate;
}`;
const fragmentShader = `
uniform vec3 palette[6];
uniform float paletteCount;
uniform float useColor;
uniform float opacity;
varying float value;
varying float highlight;
void main() {
 if(distance(gl_PointCoord,vec2(0.5))>0.5) discard;
 float p=clamp(value,0.0,0.999999)*(paletteCount-1.0);
 int i=int(floor(p));
 vec3 color=mix(palette[i],palette[i+1],fract(p));
 if(value<0.0) color=vec3(0.58,0.64,0.71);
 if(useColor<0.5) color=vec3(0.22,0.85,0.73);
 if(highlight>0.5) color=vec3(0.94,0.73,0.42);
 gl_FragColor=vec4(color,opacity);
}`;

export class CloudScene {
  private renderer: THREE.WebGLRenderer | null = null;
  private scene = new THREE.Scene();
  private camera = new THREE.Camera();
  private material = new THREE.ShaderMaterial({
    vertexShader,
    fragmentShader,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    uniforms: {
      yaw: { value: 0 },
      pitch: { value: 0 },
      zoom: { value: 1 },
      pan: { value: new THREE.Vector2() },
      aspect: { value: 1 },
      pointSize: { value: 2 },
      pixelRatio: { value: 1 },
      useSize: { value: 0 },
      useColor: { value: 0 },
      opacity: { value: 0.7 },
      paletteCount: { value: 6 },
      palette: { value: Array.from({ length: 6 }, () => new THREE.Vector3()) },
    },
  });
  private objects: THREE.Points[] = [];
  private buffers: Float32Array[] = [];
  private context: CanvasRenderingContext2D;
  private lost = false;
  width = 600;
  height = 380;
  submitted = 0;
  kind: "webgl2" | "software" = "software";
  constructor(
    public canvas: HTMLCanvasElement,
    public overlay: HTMLCanvasElement,
  ) {
    this.context = overlay.getContext("2d")!;
    try {
      const context = canvas.getContext("webgl2", {
        alpha: false,
        antialias: true,
        preserveDrawingBuffer: true,
      });
      if (context) {
        this.renderer = new THREE.WebGLRenderer({
          canvas,
          context,
          antialias: true,
          preserveDrawingBuffer: true,
        });
        this.kind = "webgl2";
      }
    } catch {
      this.renderer = null;
    }
    canvas.addEventListener("webglcontextlost", this.contextLost);
    canvas.addEventListener("webglcontextrestored", this.contextRestored);
    canvas.style.display = this.renderer ? "block" : "none";
  }
  private contextLost = (event: Event) => {
    event.preventDefault();
    this.lost = true;
    this.kind = "software";
    this.canvas.style.display = "none";
  };
  private contextRestored = () => {
    this.lost = false;
    this.kind = this.renderer ? "webgl2" : "software";
    this.canvas.style.display = this.renderer ? "block" : "none";
  };
  setBuffers(buffers: Float32Array[]) {
    const append =
      buffers.length >= this.buffers.length &&
      this.buffers.every((buffer, i) => buffers[i] === buffer);
    const start = append ? this.buffers.length : 0;
    if (!append) {
      for (const object of this.objects) {
        this.scene.remove(object);
        object.geometry.dispose();
      }
      this.objects = [];
    }
    this.buffers = buffers;
    if (this.renderer)
      for (const data of buffers.slice(start)) {
        const interleaved = new THREE.InterleavedBuffer(data, 8);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
          "position",
          new THREE.InterleavedBufferAttribute(interleaved, 3, 0),
        );
        geometry.setAttribute(
          "scalarColor",
          new THREE.InterleavedBufferAttribute(interleaved, 1, 3),
        );
        geometry.setAttribute(
          "scalarSize",
          new THREE.InterleavedBufferAttribute(interleaved, 1, 4),
        );
        geometry.setAttribute(
          "backgate",
          new THREE.InterleavedBufferAttribute(interleaved, 1, 7),
        );
        const points = new THREE.Points(geometry, this.material);
        points.frustumCulled = false;
        this.objects.push(points);
        this.scene.add(points);
      }
  }
  resize(width: number, height: number) {
    this.width = width;
    this.height = height;
    const dpr = window.devicePixelRatio || 1;
    this.renderer?.setPixelRatio(dpr);
    this.renderer?.setSize(width, height, false);
    this.overlay.width = Math.round(width * dpr);
    this.overlay.height = Math.round(height * dpr);
    this.overlay.style.width = width + "px";
    this.overlay.style.height = height + "px";
  }
  draw(
    metadata: ThreeDData,
    view: ThreeDView,
    graphOptions: GraphOptions = {},
  ) {
    const dpr = window.devicePixelRatio || 1,
      aspect = this.width / this.height;
    const ctx = this.context;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    this.submitted = this.buffers.reduce((n, data) => n + data.length / 8, 0);
    const colors = palettes[metadata.graph_options.palette ?? "ocean"];
    if (this.renderer && !this.lost) {
      const uniforms = this.material.uniforms;
      uniforms.yaw.value = view.yaw ?? defaultCamera.yaw;
      uniforms.pitch.value = view.pitch ?? defaultCamera.pitch;
      uniforms.zoom.value = view.zoom ?? 1;
      uniforms.pan.value.set(...(view.pan ?? [0, 0]));
      uniforms.aspect.value = aspect;
      uniforms.pixelRatio.value = dpr;
      uniforms.pointSize.value = view.point_size ?? 2;
      uniforms.opacity.value = view.opacity ?? 0.7;
      uniforms.useSize.value = view.size_by ? 1 : 0;
      uniforms.useColor.value = view.color_by ? 1 : 0;
      uniforms.paletteCount.value = colors.length;
      uniforms.palette.value.forEach((color: THREE.Vector3, i: number) => {
        const c = colors[Math.min(i, colors.length - 1)];
        color.set(c[0] / 255, c[1] / 255, c[2] / 255);
      });
      this.renderer.setClearColor(0x11171f, 1);
      this.renderer.render(this.scene, this.camera);
      this.submitted = this.renderer.info.render.points;
    } else {
      ctx.fillStyle = "#11171f";
      ctx.fillRect(0, 0, this.width, this.height);
      ctx.save();
      ctx.globalAlpha = view.opacity ?? 0.7;
      // Preserve source ordering and transparency consistently with the GPU path.
      const yaw = view.yaw ?? defaultCamera.yaw,
        pitch = view.pitch ?? defaultCamera.pitch;
      const cy = Math.cos(yaw),
        sy = Math.sin(yaw),
        cp = Math.cos(pitch),
        sp = Math.sin(pitch);
      const scale = ((view.zoom ?? 1) * this.height) / 4,
        pan = view.pan ?? [0, 0];
      for (const data of this.buffers)
        for (let i = 0; i < data.length; i += 8) {
          const a = data[i] * 2 - 1,
            b = data[i + 1] * 2 - 1,
            c = data[i + 2] * 2 - 1;
          const rx = a * cy + c * sy,
            rz = -a * sy + c * cy,
            ry = b * cp - rz * sp;
          const x = this.width / 2 + (rx - pan[0]) * scale,
            y = this.height / 2 - (ry - pan[1]) * scale;
          if (x < -12 || y < -12 || x > this.width + 12 || y > this.height + 12)
            continue;
          const rgb =
            data[i + 7] > 0.5
              ? [240, 185, 107]
              : view.color_by
                ? data[i + 3] < 0
                  ? [148, 163, 181]
                  : densityColor(data[i + 3], metadata.graph_options.palette)
                : [56, 217, 186];
          ctx.fillStyle = `rgb(${rgb.join(",")})`;
          const size =
            (view.point_size ?? 2) *
            (view.size_by ? 0.4 + data[i + 4] * 1.6 : 1);
          ctx.fillRect(x - size / 2, y - size / 2, size, size);
        }
      ctx.restore();
    }
    const pixel = (point: number[]) => {
      const p = project3D(point, view, aspect);
      return [(this.width * (p[0] + 1)) / 2, (this.height * (1 - p[1])) / 2];
    };
    const wire = (bounds: number[], color: string, width = 0.85) => {
      const corners = cubeCorners(bounds).map(pixel);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.beginPath();
      for (const [a, b] of cubeEdges) {
        ctx.moveTo(...(corners[a] as [number, number]));
        ctx.lineTo(...(corners[b] as [number, number]));
      }
      ctx.stroke();
    };
    if (view.show_cube !== false) wire([0, 1, 0, 1, 0, 1], "#637b9299");
    for (const box of metadata.boxes) {
      wire(
        box.normalized,
        box.color,
        graphOptions.gate_style?.line_width_px ?? 0.85,
      );
      if (graphOptions.gate_style?.show_labels === false) continue;
      const p = pixel([
        box.normalized[0],
        box.normalized[2],
        box.normalized[5],
      ]);
      const text = canvasGraphText(graphOptions, "gate_labels", 11, box.color);
      ctx.fillStyle = text.color;
      ctx.font = text.font;
      ctx.fillText(box.name, p[0] + 5, p[1] - 5);
    }
    if (view.show_labels !== false) {
      const names = [metadata.x, metadata.y, metadata.z],
        axisColors = ["#58cfb7", "#87b9ff", "#e9b773"];
      ctx.textAlign = "center";
      names.forEach((name, axis) => {
        const origin = [0, 0, 0],
          end = [0, 0, 0];
        end[axis] = 1;
        const p = pixel(end);
        const text = canvasGraphText(
          graphOptions,
          "axis_labels",
          11,
          axisColors[axis],
        );
        ctx.fillStyle = text.color;
        ctx.font = text.font;
        ctx.fillText(
          `${["X", "Y", "Z"][axis]} · ${name}`,
          p[0],
          p[1] + Math.max(18, text.pixels * 1.4),
        );
        for (const tick of metadata.ticks[axis]) {
          const point = origin.slice();
          point[axis] = axisFraction(
            tick.value,
            metadata.bounds[axis * 2],
            metadata.bounds[axis * 2 + 1],
          );
          const q = pixel(point);
          const tickText = canvasGraphText(
            graphOptions,
            "tick_labels",
            9,
            "#a6b5c6",
          );
          ctx.font = tickText.font;
          ctx.fillStyle = tickText.color;
          ctx.fillText(
            tick.label,
            q[0],
            q[1] - Math.max(7, tickText.pixels * 0.75),
          );
        }
      });
    }
    if (!metadata.displayed_count) {
      ctx.fillStyle = "#a6b5c6";
      ctx.font = "11px Inter, system-ui";
      ctx.textAlign = "center";
      ctx.fillText(
        "No finite events inside these axes",
        this.width / 2,
        this.height / 2,
      );
    }
  }
  snapshot() {
    const result = document.createElement("canvas");
    result.width = this.overlay.width;
    result.height = this.overlay.height;
    const ctx = result.getContext("2d")!;
    if (this.renderer && !this.lost) ctx.drawImage(this.canvas, 0, 0);
    ctx.drawImage(this.overlay, 0, 0);
    return result;
  }
  dispose() {
    this.canvas.removeEventListener("webglcontextlost", this.contextLost);
    this.canvas.removeEventListener(
      "webglcontextrestored",
      this.contextRestored,
    );
    for (const object of this.objects) object.geometry.dispose();
    this.material.dispose();
    this.renderer?.dispose();
    this.buffers = [];
  }
}
