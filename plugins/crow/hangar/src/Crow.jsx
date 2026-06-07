import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const LERP = 14;
const EXAG = {
  bank: 1.3,
  wingSpread: 1.2,
  glidePosture: 1.25,
  divePosture: 1.4,
};

/** Model forward = +Z (matches physics yaw). Euler order YXZ = heading, pitch, bank. */
export default function Crow({ bird, controls, tint = 0x1a1a22 }) {
  const group = useRef();
  const leftWing = useRef();
  const rightWing = useRef();
  const targetPos = useRef(new THREE.Vector3());
  const euler = useRef(new THREE.Euler(0, 0, 0, "YXZ"));

  const flapPower = controls?.flap_power || 0;
  const glide = controls?.glide;
  const pitchIn = controls?.pitch || 0;

  useFrame((_, delta) => {
    if (!group.current || !bird) return;
    const t = Math.min(1, LERP * delta);

    const [x, y, z] = bird.position;
    const [bodyPitch, yaw, roll] = bird.rotation;
    targetPos.current.set(x, y, z);
    group.current.position.lerp(targetPos.current, t);

    const exRoll = roll * EXAG.bank;
    const exPitch =
      bodyPitch +
      (glide ? 0.1 * EXAG.glidePosture : 0) +
      (pitchIn > 0 ? pitchIn * 0.28 * EXAG.divePosture : pitchIn * 0.12);

    euler.current.set(exPitch, yaw, exRoll);
    group.current.rotation.copy(euler.current);

    const spread = glide ? 0.68 * EXAG.wingSpread : 0.5;
    if (leftWing.current) leftWing.current.position.x = spread;
    if (rightWing.current) rightWing.current.position.x = -spread;

    const phase = bird.wing_phase || 0;
    const wingAngle = Math.sin(phase) * (0.32 + flapPower * 1.05);
    if (leftWing.current) {
      leftWing.current.rotation.z = -wingAngle;
      leftWing.current.rotation.y = glide ? -0.15 * EXAG.glidePosture : 0.22;
    }
    if (rightWing.current) {
      rightWing.current.rotation.z = wingAngle;
      rightWing.current.rotation.y = glide ? 0.15 * EXAG.glidePosture : -0.22;
    }
  });

  return (
    <group ref={group}>
      {/* Body axis = +Z (beak forward) */}
      <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
        <capsuleGeometry args={[0.35, 1.2, 6, 12]} />
        <meshStandardMaterial color={tint} metalness={0.15} roughness={0.65} />
      </mesh>
      <mesh position={[0, 0.15, 0.82]} castShadow>
        <sphereGeometry args={[0.28, 12, 10]} />
        <meshStandardMaterial color={0x111118} roughness={0.5} />
      </mesh>
      <mesh position={[0, 0.1, 1.12]} rotation={[Math.PI / 2, 0, 0]}>
        <coneGeometry args={[0.1, 0.35, 6]} />
        <meshStandardMaterial color={0x3a3028} />
      </mesh>
      <mesh ref={leftWing} position={[0.58, 0.08, 0.02]} rotation={[0, 0.22, 0]}>
        <planeGeometry args={[1.7, 0.6]} />
        <meshStandardMaterial color={0x252530} side={THREE.DoubleSide} roughness={0.7} />
      </mesh>
      <mesh ref={rightWing} position={[-0.58, 0.08, 0.02]} rotation={[0, -0.22, 0]}>
        <planeGeometry args={[1.7, 0.6]} />
        <meshStandardMaterial color={0x252530} side={THREE.DoubleSide} roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.04, -0.88]} rotation={[Math.PI / 2 + 0.35, 0, 0]}>
        <coneGeometry args={[0.18, 0.65, 5]} />
        <meshStandardMaterial color={0x181820} />
      </mesh>
    </group>
  );
}
