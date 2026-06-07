import { useCallback, useEffect, useRef, useState } from "react";

export default function CoordMap({ trail, lead, flock, roost, colony, size, visible, onResize, onToggle }) {
  const canvasRef = useRef(null);
  const panelRef = useRef(null);
  const [resizing, setResizing] = useState(false);

  const roostLoc = roost?.location || [0, 8, 0];
  const [rx, , rz] = roostLoc;
  const territory = roost?.territory_radius || 45;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !visible) return;
    const ctx = canvas.getContext("2d");
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const leadPos = lead?.position || [0, 10, -6];
    const [lx, , lz] = leadPos;
    const span = territory * 1.35;
    const toMap = (x, z) => ({
      mx: ((x - lx) / span + 0.5) * size,
      my: ((z - lz) / span + 0.5) * size,
    });

    ctx.fillStyle = "rgba(8, 12, 18, 0.9)";
    ctx.fillRect(0, 0, size, size);

    const roostPt = toMap(rx, rz);
    ctx.strokeStyle = "rgba(120, 200, 140, 0.35)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(roostPt.mx, roostPt.my, (territory / span) * size, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = "rgba(90, 180, 120, 0.85)";
    ctx.beginPath();
    ctx.arc(roostPt.mx, roostPt.my, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#9ad4a8";
    ctx.font = "9px Consolas, monospace";
    ctx.fillText("ROOST", roostPt.mx + 8, roostPt.my + 3);

    ctx.strokeStyle = "rgba(90, 120, 160, 0.2)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const t = (i / 4) * size;
      ctx.beginPath();
      ctx.moveTo(t, 0);
      ctx.lineTo(t, size);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, t);
      ctx.lineTo(size, t);
      ctx.stroke();
    }

    if (trail.length > 1) {
      ctx.strokeStyle = "rgba(120, 180, 255, 0.75)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      trail.forEach((p, i) => {
        const { mx, my } = toMap(p.x, p.z);
        if (i === 0) ctx.moveTo(mx, my);
        else ctx.lineTo(mx, my);
      });
      ctx.stroke();
    }

    (flock || []).forEach((bird, i) => {
      if (i === 0) return;
      const [x, , z] = bird.position || [0, 0, 0];
      const { mx, my } = toMap(x, z);
      ctx.fillStyle = "#4a5568";
      ctx.beginPath();
      ctx.arc(mx, my, 3, 0, Math.PI * 2);
      ctx.fill();
    });

    const vel = lead?.velocity || [0, 0, 0];
    const rot = lead?.rotation || [0, 0, 0];
    const [vx, , vz] = vel;
    const speed = Math.hypot(vx, vz);
    const headingRad = speed > 0.25 ? Math.atan2(vx, vz) : rot[1] || 0;
    const pilot = toMap(lx, lz);

    function drawDirectionArrow(mx, my, angle, len, color) {
      ctx.save();
      ctx.translate(mx, my);
      ctx.rotate(angle);
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(0, len);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, len);
      ctx.lineTo(-5, len - 9);
      ctx.lineTo(5, len - 9);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }

    const arrowLen = Math.min(28, 12 + speed * 2.5);
    drawDirectionArrow(pilot.mx, pilot.my, headingRad, arrowLen, "#ffd27a");

    ctx.fillStyle = "#7eb8ff";
    ctx.strokeStyle = "#1a2a3a";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(pilot.mx, pilot.my, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    const [x, y, z] = leadPos;
    const heading = `${((headingRad * 180) / Math.PI).toFixed(0)}°`;
    ctx.fillStyle = "#c8d0dc";
    ctx.font = "10px Consolas, monospace";
    ctx.fillText(`X ${x.toFixed(1)}`, 8, size - 58);
    ctx.fillText(`Y ${y.toFixed(1)}`, 8, size - 46);
    ctx.fillText(`Z ${z.toFixed(1)}`, 8, size - 34);
    ctx.fillText(`Hdg ${heading}`, 8, size - 22);
    ctx.fillText(`Spd ${speed.toFixed(1)}`, 8, size - 10);

    if (colony?.phase_note) {
      ctx.fillStyle = "rgba(158, 199, 255, 0.85)";
      ctx.font = "9px Segoe UI, sans-serif";
      const note = colony.phase_note.length > 34 ? `${colony.phase_note.slice(0, 31)}…` : colony.phase_note;
      ctx.fillText(note, 8, 14);
    }
  }, [trail, lead, flock, roost, colony, size, visible, rx, rz, territory]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    if (!resizing) return;
    function onMove(e) {
      const panel = panelRef.current;
      if (!panel) return;
      const rect = panel.getBoundingClientRect();
      const next = Math.max(120, Math.min(320, e.clientX - rect.left + 8));
      onResize(next);
    }
    function onUp() {
      setResizing(false);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [resizing, onResize]);

  if (!visible) {
    return (
      <button type="button" id="map-toggle-hidden" onClick={onToggle}>
        Map (M)
      </button>
    );
  }

  return (
    <div id="coord-map" ref={panelRef} style={{ width: size + 16 }}>
      <div className="coord-map-header">
        <span className="coord-map-title">Flight map · roost-relative</span>
        <button type="button" className="coord-map-hide" onClick={onToggle} title="Hide (M)">
          ×
        </button>
      </div>
      <canvas ref={canvasRef} width={size} height={size} style={{ width: size, height: size }} />
      <div
        className="coord-map-resize"
        onMouseDown={() => setResizing(true)}
        title="Drag to resize"
      />
    </div>
  );
}
