#!/usr/bin/env python3
"""Validate a robot manifest and render Compose from image-embedded metadata."""
import argparse
import os
import re
import shlex
import sys
from pathlib import Path

import yaml

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UNRESOLVED_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fail(message):
    raise ValueError(message)


def load(path):
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        fail(f"Cannot read {path}: {error}")
    if not isinstance(value, dict):
        fail(f"{path} must be a YAML mapping")
    return value


def mapping(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail(f"{name} must be a mapping")
    return value


def env(value, name):
    result = {}
    for key, item in mapping(value, name).items():
        if not isinstance(key, str) or not ENV.fullmatch(key) or item is None:
            fail(f"Invalid variable in {name}: {key!r}")
        text = os.path.expandvars(str(item))
        if "\n" in text or "\r" in text:
            fail(f"{name}.{key} cannot contain a line break")
        unresolved = UNRESOLVED_ENV.search(text)
        if unresolved:
            fail(f"{name}.{key} references unset environment variable {unresolved.group(1)!r}")
        result[key] = text
    return result


def string_list(value, name):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        fail(f"{name} must be a list of non-empty strings")
    return value


def bool_value(value, name):
    if not isinstance(value, bool):
        fail(f"{name} must be true or false")
    return value


def runtime_options(value, name):
    runtime = mapping(value, name)
    supported = {
        "privileged",
        "read_only",
        "volumes",
        "devices",
        "device_cgroup_rules",
        "cap_add",
        "group_add",
        "security_opt",
        "tmpfs",
    }
    unknown = set(runtime).difference(supported)
    if unknown:
        fail(f"{name} has unsupported keys: {', '.join(sorted(unknown))}")
    result = {}
    for key in ("privileged", "read_only"):
        if key in runtime:
            result[key] = bool_value(runtime[key], f"{name}.{key}")
    for key in ("volumes", "devices", "device_cgroup_rules", "cap_add", "group_add", "security_opt", "tmpfs"):
        if key in runtime:
            result[key] = string_list(runtime[key], f"{name}.{key}")
    return result


def merge_runtime(service, runtime, *, replace_security=False):
    for key in ("privileged", "read_only"):
        if key in runtime:
            service[key] = runtime[key]
    for key in ("volumes", "devices", "device_cgroup_rules", "cap_add", "group_add", "tmpfs"):
        if key not in runtime:
            continue
        values = service.setdefault(key, [])
        for item in runtime[key]:
            if item not in values:
                values.append(item)
    if "security_opt" in runtime:
        if replace_security:
            service["security_opt"] = list(runtime["security_opt"])
        else:
            values = service.setdefault("security_opt", [])
            for item in runtime["security_opt"]:
                if item not in values:
                    values.append(item)


def components(manifest):
    result = mapping(manifest.get("components"), "components")
    if not result:
        fail("components must declare at least one image")
    for name, value in result.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            fail(f"Invalid component name: {name!r}")
        value = mapping(value, f"components.{name}")
        image = value.get("image")
        if not isinstance(image, str) or "@sha256:" not in image or image.endswith("@sha256:" + "0" * 64):
            fail(f"components.{name}.image must contain a non-placeholder digest")
        enabled = value.get("enabled_at_boot", [])
        if not isinstance(enabled, list) or not all(isinstance(x, str) for x in enabled):
            fail(f"components.{name}.enabled_at_boot must be a YAML list of launch names")
        yield name, value


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--list-images", action="store_true")
    parser.add_argument("--metadata-dir", type=Path)
    parser.add_argument("--compose-output", type=Path)
    parser.add_argument("--env-output", type=Path)
    args = parser.parse_args()
    manifest = load(args.manifest)
    declared = list(components(manifest))
    if args.list_images:
        for name, value in declared:
            print(f"{name}\t{value['image']}")
        return 0
    if not all((args.metadata_dir, args.compose_output, args.env_output)):
        fail("--metadata-dir, --compose-output and --env-output are required when rendering")

    global_env = env(manifest.get("environment"), "environment")
    services = {}
    for component, value in declared:
        metadata = mapping(load(args.metadata_dir / f"{component}.yaml").get("repository"), f"{component} metadata")
        if metadata.get("name") != component:
            fail(f"Embedded metadata does not describe component {component}")
        launches = mapping(metadata.get("launches"), f"{component}.launches")
        metadata_runtime = runtime_options(metadata.get("runtime"), f"{component}.runtime")
        manifest_runtime = runtime_options(value.get("runtime"), f"components.{component}.runtime")
        enabled = set(value["enabled_at_boot"])
        unknown = enabled.difference(launches)
        if unknown:
            fail(f"components.{component}.enabled_at_boot names unknown launches: {', '.join(sorted(unknown))}")
        for launch_name, launch in launches.items():
            if not isinstance(launch_name, str) or not NAME.fullmatch(launch_name):
                fail(f"Invalid launch name in {component}: {launch_name!r}")
            launch = mapping(launch, f"{component}.launches.{launch_name}")
            package, launch_file = launch.get("package"), launch.get("file")
            if not isinstance(package, str) or not isinstance(launch_file, str):
                fail(f"{component}.{launch_name} requires package and file")
            params = launch.get("parameters_file", "")
            if not isinstance(params, str):
                fail(f"{component}.{launch_name}.parameters_file must be a string")
            # A systemd unit is generated from this name.  Keep the same
            # readable single-hyphen convention as development services.
            service = f"{component}-{launch_name}"
            service_env = {"SORACCEL_LAUNCH_PACKAGE": package, "SORACCEL_LAUNCH_FILE": launch_file}
            if params:
                service_env["SORACCEL_PARAMETERS_FILE"] = f"/config/{params.lstrip('/').split('/')[-1]}"
            services[service] = {
                "image": value["image"], "container_name": f"soraccel-{service}",
                "network_mode": "host", "runtime": "nvidia", "init": True, "read_only": True,
                "env_file": [str(args.env_output)], "environment": service_env,
                "volumes": ["/etc/soraccel/config:/config:ro", "/var/log/soraccel:/var/log/soraccel"],
                "tmpfs": ["/tmp:mode=1777,size=256m"], "security_opt": ["no-new-privileges:true"],
                "labels": {"com.soraccel.component": component, "com.soraccel.launch": launch_name,
                           "com.soraccel.enabled-at-boot": str(launch_name in enabled).lower()},
            }
            merge_runtime(services[service], metadata_runtime)
            merge_runtime(services[service], manifest_runtime, replace_security=True)
            if services[service].get("privileged") is True and services[service].get("security_opt") == ["no-new-privileges:true"]:
                services[service].pop("security_opt")
    write(args.compose_output, yaml.safe_dump({"services": services}, sort_keys=False))
    write(args.env_output, "".join(f"{key}={shlex.quote(value)}\n" for key, value in global_env.items()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(f"render-deployment: {error}", file=sys.stderr)
        raise SystemExit(65)
