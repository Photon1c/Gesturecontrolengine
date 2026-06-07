import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const COUNT = 220;

export default function WindParticles({ speed, leadPosition }) {
  const points = useRef();
  const speeds = useMemo(() => {
    const s = new Float32Array(COUNT);
    for (let i = 0; i < COUNT; i += 1) s[i] = 0.6 + Math.random() * 1.4;
    return s;
  }, []);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const arr = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i += 1) {
      arr[i * 3] = (Math.random() - 0.5) * 40;
      arr[i * 3 + 1] = Math.random() * 18 + 4;
      arr[i * 3 + 2] = (Math.random() - 0.5) * 40;
    }
    geo.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    return geo;
  }, []);

  useFrame((_, delta) => {
    if (!points.current) return;
    const pos = geometry.attributes.position.array;
    const intensity = Math.min(1, speed / 8);
    const lx = leadPosition?.[0] || 0;
    const ly = leadPosition?.[1] || 10;
    const lz = leadPosition?.[2] || 0;

    for (let i = 0; i < COUNT; i += 1) {
      if (intensity < 0.12) continue;
      const stride = i * 3;
      pos[stride] += speeds[i] * intensity * delta * 14;
      pos[stride + 2] += delta * 2;

      if (pos[stride] > lx + 24) pos[stride] = lx - 24 - Math.random() * 8;
      if (pos[stride + 1] > ly + 16 || pos[stride + 1] < ly - 4) {
        pos[stride + 1] = ly + 4 + Math.random() * 10;
      }
      if (pos[stride + 2] > lz + 20) pos[stride + 2] = lz - 20;
    }
    geometry.attributes.position.needsUpdate = true;
    points.current.material.opacity = intensity * 0.55;
  });

  return (
    <points ref={points} geometry={geometry}>
      <pointsMaterial
        color="#e8f0ff"
        size={0.12}
        transparent
        opacity={0}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </points>
  );
}
