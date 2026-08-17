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

## Network setup

Network configuration is host-specific. It belongs in `soraccel_setup`, not in
component repositories. The bootstrap can configure it once, then persist the
selected values in:

```text
/etc/soraccel/robot.env
```

Interactive bootstrap:

```bash
curl -fsSL https://raw.githubusercontent.com/Soraccel/soraccel_setup/main/scripts/bootstrap-robot \
  | sudo bash -s -- \
      --robot-id test_robot \
      --setup-ref main \
      --deployment-ref main \
      --configure-network
```

Non-interactive bootstrap for the current B2W/Nano test topology:

```bash
curl -fsSL https://raw.githubusercontent.com/Soraccel/soraccel_setup/main/scripts/bootstrap-robot \
  | sudo bash -s -- \
      --robot-id test_robot \
      --setup-ref main \
      --deployment-ref main \
      --configure-network \
      --network-non-interactive \
      --pc-interface wlP1p1s0 \
      --unitree-interface enP8p1s0 \
      --unitree-address 192.168.123.51/24 \
      --lidar-interface enP8p1s0 \
      --lidar-host-address 192.168.1.102/24 \
      --lidar-sensor-address 192.168.1.20 \
      --lidar-msop-port 6699 \
      --lidar-difop-port 7788
```

The script deliberately does not force a static address on the ROS/operator
interface unless `--pc-address` is provided. This avoids breaking a Wi-Fi or
DHCP SSH session during bootstrap. The Unitree/LiDAR interface is configured as
static and `ipv4.never-default yes`, so it does not steal the default Internet
route.

To reconfigure an already installed robot:

```bash
sudo /opt/soraccel/setup/scripts/configure-network
```

or, non-interactively:

```bash
sudo /opt/soraccel/setup/scripts/configure-network \
  --non-interactive \
  --pc-interface wlP1p1s0 \
  --unitree-interface enP8p1s0 \
  --unitree-address 192.168.123.51/24 \
  --lidar-interface enP8p1s0 \
  --lidar-host-address 192.168.1.102/24 \
  --lidar-sensor-address 192.168.1.20 \
  --lidar-msop-port 6699 \
  --lidar-difop-port 7788
```

Dry-run:

```bash
sudo /opt/soraccel/setup/scripts/configure-network \
  --dry-run \
  --non-interactive \
  --pc-interface wlP1p1s0 \
  --unitree-interface enP8p1s0
```

Useful checks:

```bash
ip -br addr
ip route
ping -c 3 192.168.1.20
sudo tcpdump -ni enP8p1s0 'host 192.168.1.20 or udp port 6699 or udp port 7788'
```

The following variables are written to `/etc/soraccel/robot.env`:

```bash
SORACCEL_PC_INTERFACE=wlP1p1s0
SORACCEL_UNITREE_INTERFACE=enP8p1s0
SORACCEL_UNITREE_ADDRESS=192.168.123.51/24
SORACCEL_LIDAR_INTERFACE=enP8p1s0
SORACCEL_LIDAR_HOST_ADDRESS=192.168.1.102/24
SORACCEL_LIDAR_HOST_IP=192.168.1.102
SORACCEL_LIDAR_SENSOR_ADDRESS=192.168.1.20
SORACCEL_LIDAR_MSOP_PORT=6699
SORACCEL_LIDAR_DIFOP_PORT=7788
```

`apply` exports these variables before rendering the deployment and before
installing robot config files under `/etc/soraccel/config`. Therefore both
`robots/<robot-id>/deployment.yaml` and `robots/<robot-id>/config/*.yaml` can
use them explicitly, for example:

```yaml
environment:
  CYCLONEDDS_URI: '<CycloneDDS><Domain><General><Interfaces><NetworkInterface name="${SORACCEL_PC_INTERFACE}" priority="default" multicast="default" /></Interfaces></General></Domain></CycloneDDS>'
  SORACCEL_LIDAR_HOST_IP: ${SORACCEL_LIDAR_HOST_IP}
  SORACCEL_LIDAR_MSOP_PORT: ${SORACCEL_LIDAR_MSOP_PORT}
  SORACCEL_LIDAR_DIFOP_PORT: ${SORACCEL_LIDAR_DIFOP_PORT}
```

Component configs can then consume these environment values. For example
`sensors.yaml` may use:

```yaml
host_address: ${SORACCEL_LIDAR_HOST_IP}
msop_port: ${SORACCEL_LIDAR_MSOP_PORT}
difop_port: ${SORACCEL_LIDAR_DIFOP_PORT}
```

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
