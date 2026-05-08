# JARVIS Assistant Mode

## Overview

JARVIS is a butler-engineer assistant integrated into Gesturecontrolengine.
It uses the existing camera-based gesture detection plus **audio clap detection**
to activate 4 plugin modules that manage your workflow.

## Quick Start

```bash
# Install extra dependencies
pip install pyttsx3 pyaudio

# Run with JARVIS mode
python sensor_engine.py --jarvis --jarvis-config jarvis_config.json --debug-overlay
```

## Gesture Map

| Gesture | Trigger | Plugin Response |
|---------|---------|----------------|
| Double hand clap | Audio | Wakeup: monitors on + TTS briefing |
| `arm_execute` → `confirm_execute` | Visual | All plugins check for pending actions |
| Arms raised | Visual | Triggers status checks across plugins |
| Open palm (pause) | Visual | Suppresses proactive notifications |

## Configuration

Edit `jarvis_config.json`:

### Wakeup
```json
{
  "wakeup": {
    "enabled": true,
    "double_clap_window_seconds": 1.5,
    "vocal_readout": true,
    "latitude": 37.7749,
    "longitude": -122.4194,
    "tts": { "voice": "default", "rate": 180 }
  }
}
```

### Atmosphere
```json
{
  "atmosphere": {
    "enabled": true,
    "philips_hue": {
      "bridge_ip": "192.168.1.100",
      "api_key": "your_hue_api_key",
      "light_ids": [1, 2, 3]
    },
    "spotify": {
      "client_id": "...",
      "client_secret": "...",
      "playlists": { "focus": "spotify:playlist:...", "relax": "...", "energize": "..." }
    }
  }
}
```

### Devshop
```json
{
  "devshop": {
    "enabled": true,
    "watch_directories": ["/home/user/project-a", "/home/user/project-b"]
  }
}
```

### Project
```json
{
  "project": {
    "enabled": true,
    "projects": [
      {"name": "Release v2.0", "deadline": "2026-06-01"},
      {"name": "Bug bash", "deadline": "2026-05-15"}
    ]
  }
}
```

## Architecture

```
Audio (microphone) ──> AudioClapDetector ──┐
                                           ├──> JarvisOrchestrator ──> WakeupPlugin
Camera ──> MediaPipe pose/hands ──> GestureDetector ──┘         ├──> AtmospherePlugin
                                                                 ├──> DevshopPlugin
                                                                 └──> ProjectPlugin
```

All plugins inherit from `JarvisPlugin` base class and implement `on_gesture()`, `on_tick()`, and `status()`.
