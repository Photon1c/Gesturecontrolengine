import { useEffect, useRef } from "react";
import * as THREE from "three";

export default function FlightPathTrail({ trail }) {
  const lineRef = useRef();
  const geometry = useRef(new THREE.BufferGeometry());

  useEffect(() => {
    if (!trail.length) return;
    const arr = new Float32Array(trail.length * 3);
    trail.forEach((p, i) => {
      arr[i * 3] = p.x;
      arr[i * 3 + 1] = p.y;
      arr[i * 3 + 2] = p.z;
    });
    geometry.current.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    geometry.current.setDrawRange(0, trail.length);
    geometry.current.computeBoundingSphere();
    if (lineRef.current) lineRef.current.geometry = geometry.current;
  }, [trail]);

  if (trail.length < 2) return null;

  return (
    <line ref={lineRef} geometry={geometry.current}>
      <lineBasicMaterial color="#9ec7ff" transparent opacity={0.65} />
    </line>
  );
}
