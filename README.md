# Soraccel setup

Public, versioned robot bootstrap and runtime agent. This repository contains
the operational scripts; it deliberately contains no robot image selection or
robot-specific configuration.

## Responsibilities

- install Docker, Compose and the NVIDIA runtime prerequisites;
- install a pinned setup revision under `/opt/soraccel/setup`;
- install a separately pinned deployment manifest under
  `/opt/soraccel/deployment`;
- render the deployment manifest into Compose and user-systemd services;
- update a deployment revision without modifying the installed setup scripts.

## Bootstrap

Use immutable refs for both repositories:

```bash
curl -fsSL https://raw.githubusercontent.com/Soraccel/soraccel_setup/main/scripts/bootstrap-robot \
  | sudo bash -s -- \
      --robot-id test_robot \
      --setup-ref v0.1.0 \
      --deployment-ref <approved-tag-or-commit>
```

The script asks for the limited `soraccel-robot` GHCR token on the terminal and
stores it outside Git. It never builds ROS source on the robot.

For a robot without NTP or a valid RTC, pass a known UTC time when executing a
locally provisioned copy of the bootstrap script:

```bash
sudo ./bootstrap-robot ... --date-time '2026-07-28 14:00:00 UTC'
```

It also installs `soraccel-sync-time.service`, adapted from the Munwag HTTPS
Date recovery service. At boot, it recovers the date from an HTTPS `Date`
header before the normal robot stack is used.

## Robot layout

```text
/opt/soraccel/setup/        # pinned operational scripts
/opt/soraccel/deployment/   # pinned desired-state manifest
/etc/soraccel/config/       # mirror of the selected deployment revision
```

At boot, the user service `soraccel-reconcile.service` runs `apply --no-pull`.
It reads the installed deployment revision but does not contact Git or GHCR.
Non-secret files from `robots/<robot-id>/config/` are mirrored into
`/etc/soraccel/config/`; do not edit that target manually.
`apply` also reconciles `SORACCEL_ROBOT_ID` into interactive Bash shells and
the host user-systemd environment.

If the selected deployment manifest includes the `localization` component,
`apply` also ensures that an Exwayz license token exists locally. On the first
interactive run it prompts:

```text
Exwayz license token:
```

The token is stored outside Git in:

```text
~/.config/soraccel/secrets/exwayz.env
```

Then `apply` injects `EXWAYZ_LICENSE_KEY` into the rendered runtime environment
used by the generated Docker Compose services. Subsequent boots do not prompt:
`soraccel-reconcile.service` reuses the stored secret. If the secret is missing
and `apply` runs non-interactively, it fails deliberately instead of starting
`localization` without a license.

If the manifest includes the `sensors` component, `apply` also checks the camera
USB mapping in `robots/<robot-id>/config/sensors.yaml`. A stable mapping should
use `/dev/v4l/by-path/...` for UVC cameras and `usb_port_id` for RealSense
cameras. If the config still contains volatile `/dev/videoX` devices or missing
RealSense ports, `apply` runs an interactive detector:

```bash
/opt/soraccel/setup/scripts/configure-sensors-usb --robot-id test_robot
```

The detector lists the connected UVC and Intel RealSense devices and asks which
physical device should be assigned to each configured camera name
(`rgb_front`, `rgb_rear`, `depth_front`, `depth_rear`, ...). It writes the
result back to the deployment checkout, then `apply` mirrors it into
`/etc/soraccel/config/sensors.yaml`. Subsequent boots do not prompt unless the
config becomes incomplete again.

To inspect the detected topology without writing:

```bash
/opt/soraccel/setup/scripts/configure-sensors-usb \
  --robot-id test_robot \
  --print-only
```

For an intentional rollout:

```bash
sudo /opt/soraccel/setup/scripts/install-deployment-revision \
  --deployment-ref <approved-tag-or-commit>
/opt/soraccel/setup/scripts/apply
```
