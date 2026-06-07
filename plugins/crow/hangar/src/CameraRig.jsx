import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const _euler = new THREE.Euler(0, 0, 0, "YXZ");
const _offset = new THREE.Vector3();
const _anchor = new THREE.Vector3();

/**
 * Chase = behind + slightly above the lead bird; look target leads the flight path.
 */
function isUnsetLead(pos) {
  if (!pos || pos.length < 3) return true;
  const [x, y, z] = pos;
  return Math.abs(x) < 0.05 && Math.abs(y) < 0.05 && Math.abs(z) < 0.05;
}

export default function CameraRig({ lead, config, mode, roost, flockCount = 0 }) {
  const focus = useRef(new THREE.Vector3());
  const desired = useRef(new THREE.Vector3());

  useFrame((state, delta) => {
    const roostLoc = roost?.location || [0, 8, 0];
    const leadPos = lead?.position;
    const useRoostAnchor = flockCount < 1 || isUnsetLead(leadPos);
    const anchorPos = useRoostAnchor
      ? [roostLoc[0], roostLoc[1] + 6, roostLoc[2] + 8]
      : leadPos || [roostLoc[0], roostLoc[1] + 6, roostLoc[2] + 8];
    const leadRot = lead?.rotation || [0, 0, 0];
    const leadVel = lead?.velocity || [0, 0, 0];
    const [, yawFromPose, roll] = leadRot;
    const [vx, , vz] = leadVel;
    const hSpeed = Math.hypot(vx, vz);

    const heading = hSpeed > 0.25 ? Math.atan2(vx, vz) : yawFromPose;
    const lookAhead = config.lookAhead ?? 8;
    const birdLift = config.birdLift ?? 1.0;

    focus.current.set(
      anchorPos[0] + Math.sin(heading) * lookAhead,
      anchorPos[1] + (config.focusHeight ?? 0.8),
      anchorPos[2] + Math.cos(heading) * lookAhead
    );

    _anchor.set(anchorPos[0], anchorPos[1] + birdLift, anchorPos[2]);

    if (useRoostAnchor) {
      desired.current.set(
        roostLoc[0] + 6,
        roostLoc[1] + 10,
        roostLoc[2] + 22
      );
      focus.current.set(roostLoc[0], roostLoc[1] + 5, roostLoc[2] + 6);
    } else if (mode === "commander") {
      desired.current.set(
        anchorPos[0],
        anchorPos[1] + config.height,
        anchorPos[2] + config.distance * 0.15
      );
    } else if (mode === "fpv") {
      _offset.set(0, 0.35, 0.6);
      _euler.set(0, heading, 0);
      _offset.applyEuler(_euler);
      desired.current.copy(_anchor).add(_offset);
      focus.current.set(anchorPos[0], anchorPos[1] + 0.5, anchorPos[2]);
      focus.current.x += Math.sin(heading) * 10;
      focus.current.z += Math.cos(heading) * 10;
    } else if (mode === "shoulder") {
      _offset.set(config.lateral ?? 2.8, config.height, -(config.distance * 0.65));
      _euler.set(0, heading, 0);
      _offset.applyEuler(_euler);
      desired.current.copy(_anchor).add(_offset);
    } else {
      _offset.set(0, config.height, -config.distance);
      _euler.set(0, heading, 0);
      _offset.applyEuler(_euler);
      desired.current.copy(_anchor).add(_offset);
      if (hSpeed > 0.8) {
        _offset.set(0, 0, -hSpeed * (config.speedPullback ?? 0.08));
        _euler.set(0, heading, 0);
        _offset.applyEuler(_euler);
        desired.current.add(_offset);
      }
    }

    const smooth = 1 - Math.exp(-config.lag * delta);
    state.camera.position.lerp(desired.current, smooth);
    state.camera.lookAt(focus.current);
    state.camera.rotation.z = THREE.MathUtils.lerp(
      state.camera.rotation.z,
      mode === "chase" || mode === "shoulder" ? roll * (config.bankAmount ?? 0.18) : 0,
      smooth * 0.45
    );

    state.camera.fov = THREE.MathUtils.lerp(state.camera.fov, config.fov, smooth);
    state.camera.updateProjectionMatrix();
  });

  return null;
}
