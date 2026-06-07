import CrowScene from "./CrowScene.jsx";
import Hud from "./Hud.jsx";
import CoordMap from "./CoordMap.jsx";
import { useTelemetry } from "./useTelemetry.js";
import { useCameraMode } from "./useCameraMode.js";
import { useFlightHistory } from "./useFlightHistory.js";
import { useMapPanel } from "./useMapPanel.js";
import { useAutoMode } from "./useAutoMode.js";
import { useLaunchControl } from "./useLaunchControl.js";
import LaunchPanel from "./LaunchPanel.jsx";

export default function App() {
  const { state, error } = useTelemetry();
  const { autoMode, controlMode, setAutoMode, pending, error: autoError } = useAutoMode(state);
  const { count, maxCrows, launch, resetFlock, spawnCrow } = useLaunchControl(state);
  const { mode, config } = useCameraMode();
  const trail = useFlightHistory(state);
  const { visible, size, toggle, resize } = useMapPanel();

  return (
    <>
      <Hud
        state={state}
        error={error}
        cameraLabel={config.label}
        autoMode={autoMode}
        controlMode={controlMode}
        setAutoMode={setAutoMode}
        autoPending={pending}
        autoError={autoError}
      />
      <LaunchPanel
        launch={launch}
        count={count}
        maxCrows={maxCrows}
        onReset={resetFlock}
        onSpawn={spawnCrow}
      />
      <CoordMap
        trail={trail}
        lead={state?.lead}
        flock={state?.flock}
        roost={state?.colony?.roost}
        colony={state?.colony}
        size={size}
        visible={visible}
        onResize={resize}
        onToggle={toggle}
      />
      <CrowScene state={state} cameraMode={mode} cameraConfig={config} trail={trail} />
    </>
  );
}
