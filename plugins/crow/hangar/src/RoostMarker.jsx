export default function RoostMarker({ roost }) {
  if (!roost?.location) return null;
  const [x, y, z] = roost.location;
  const radius = roost.territory_radius || 45;

  return (
    <group position={[x, y, z]}>
      <mesh position={[0, 2.5, 0]} castShadow>
        <cylinderGeometry args={[0.35, 0.55, 5, 8]} />
        <meshStandardMaterial color="#4a3728" roughness={0.9} />
      </mesh>
      <mesh position={[0, 5.5, 0]}>
        <sphereGeometry args={[2.8, 10, 8]} />
        <meshStandardMaterial color="#2d5a34" roughness={0.85} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0]}>
        <ringGeometry args={[radius * 0.85, radius, 48]} />
        <meshBasicMaterial color="#7ec99a" transparent opacity={0.18} side={2} />
      </mesh>
    </group>
  );
}
