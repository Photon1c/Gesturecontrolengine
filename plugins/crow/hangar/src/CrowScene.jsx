import { Canvas } from "@react-three/fiber";
import { Grid, Sky } from "@react-three/drei";
import Crow from "./Crow.jsx";
import CameraRig from "./CameraRig.jsx";
import WindParticles from "./WindParticles.jsx";
import CloudLayer from "./CloudLayer.jsx";
import FlightPathTrail from "./FlightPathTrail.jsx";
import RoostMarker from "./RoostMarker.jsx";

function World({ flock, controls, trail, speed, leadPos, roost }) {
  return (
    <>
      <Sky distance={450000} sunPosition={[30, 90, 20]} turbidity={6} rayleigh={1.2} />
      <ambientLight intensity={0.42} />
      <directionalLight
        castShadow
        intensity={1.25}
        position={[30, 50, 15]}
        color="#fff2d0"
      />
      <fog attach="fog" args={["#7a9ab8", 35, 140]} />
      <CloudLayer />
      <RoostMarker roost={roost} />
      <WindParticles speed={speed} leadPosition={leadPos} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[240, 240]} />
        <meshStandardMaterial color="#3d5a45" roughness={0.95} />
      </mesh>
      <Grid
        position={[0, 0.02, 0]}
        args={[160, 80]}
        cellColor="#4a6070"
        sectionColor="#3a4a58"
      />
      <FlightPathTrail trail={trail} />
      {(flock || []).map((bird, i) => (
        <Crow
          key={bird.id}
          bird={bird}
          controls={controls}
          tint={i === 0 ? 0x1a1a22 : 0x2a2830}
        />
      ))}
    </>
  );
}

export default function CrowScene({ state, cameraMode, cameraConfig, trail }) {
  const flock = state?.flock || [];
  const controls = state?.controls || {};
  const lead = state?.lead || {};
  const leadPos = flock[0]?.position || lead.position;
  const vel = lead.velocity || [0, 0, 0];
  const speed = Math.hypot(vel[0], vel[1], vel[2]);
  const roost = state?.colony?.roost;

  return (
    <Canvas
      shadows
      camera={{ position: [0, 14, 22], fov: cameraConfig.fov, near: 0.1, far: 400 }}
      gl={{ antialias: true }}
    >
      <color attach="background" args={["#6a8aaa"]} />
      <World
        flock={flock}
        controls={controls}
        trail={trail}
        speed={speed}
        leadPos={leadPos}
        roost={roost}
      />
      <CameraRig
        lead={lead}
        config={cameraConfig}
        mode={cameraMode}
        roost={roost}
        flockCount={flock.length}
      />
    </Canvas>
  );
}
