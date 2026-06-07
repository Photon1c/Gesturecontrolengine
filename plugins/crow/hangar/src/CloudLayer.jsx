export default function CloudLayer() {
  const clouds = [
    { pos: [-30, 22, -40], scale: 12 },
    { pos: [25, 26, -55], scale: 16 },
    { pos: [0, 30, -70], scale: 20 },
    { pos: [-45, 18, -20], scale: 10 },
    { pos: [40, 24, -30], scale: 14 },
  ];

  return (
    <group>
      {clouds.map((c, i) => (
        <group key={i} position={c.pos} scale={c.scale}>
          <mesh>
            <sphereGeometry args={[1, 10, 8]} />
            <meshStandardMaterial color="#f4f8ff" transparent opacity={0.55} flatShading />
          </mesh>
          <mesh position={[0.6, 0.1, 0.2]}>
            <sphereGeometry args={[0.7, 8, 6]} />
            <meshStandardMaterial color="#eef4fc" transparent opacity={0.5} flatShading />
          </mesh>
          <mesh position={[-0.5, -0.05, -0.1]}>
            <sphereGeometry args={[0.65, 8, 6]} />
            <meshStandardMaterial color="#eef4fc" transparent opacity={0.48} flatShading />
          </mesh>
        </group>
      ))}
    </group>
  );
}
