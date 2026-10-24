"""版本清单、版本 JSON、继承合并、库文件与规则解析。"""

from __future__ import annotations

import json
import platform as py_platform
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import net
from .mirror import OFFICIAL_MANIFEST

MANIFEST_URL = OFFICIAL_MANIFEST


# ---------------------------------------------------------------- 运行平台


@dataclass(frozen=True)
class Platform:
    os_name: str
    arch: str
    os_version: str
    bits: int
    features: dict[str, bool] = field(default_factory=dict, compare=False, hash=False)

    @classmethod
    def current(cls) -> "Platform":
        system = py_platform.system().lower()
        os_name = {"windows": "windows", "darwin": "osx", "linux": "linux"}.get(system, system)
        machine = py_platform.machine().lower()
        if machine in ("amd64", "x86_64"):
            arch, bits = "x86_64", 64
        elif machine in ("arm64", "aarch64"):
            arch, bits = "arm64", 64
        elif machine in ("i386", "i686", "x86"):
            arch, bits = "x86", 32
        else:
            arch, bits = machine or "x86_64", 64 if "64" in machine else 32
        return cls(os_name=os_name, arch=arch, os_version=py_platform.version(), bits=bits)


def rules_allow(rules: list[dict] | None, platform: Platform) -> bool:
    """按 Mojang rules 判断当前平台是否允许该库。"""
    if not rules:
        return True
    allowed = False
    for rule in rules:
        action = rule.get("action", "allow")
        os_rule = rule.get("os") or {}
        matched = True
        if os_rule:
            if os_rule.get("name") and os_rule["name"] != platform.os_name:
                matched = False
            if os_rule.get("arch") and os_rule["arch"] != platform.arch:
                matched = False
            if os_rule.get("version"):
                try:
                    if not re.search(os_rule["version"], platform.os_version):
                        matched = False
                except re.error:
                    matched = False
        features = rule.get("features")
        if matched and features:
            matched = all(platform.features.get(key, False) == value for key, value in features.items())
        if matched:
            allowed = action == "allow"
    return allowed


# ---------------------------------------------------------------- 版本清单


def fetch_manifest(mirror: str = "official") -> dict:
    return net.get_json(MANIFEST_URL, mirror)


def find_version(manifest: dict, version_id: str) -> dict:
    for entry in manifest.get("versions", []):
        if entry["id"] == version_id:
            return entry
    raise KeyError(f"未找到版本 {version_id}")


def fetch_version(version_id: str, manifest: dict, mirror: str = "official") -> dict:
    entry = find_version(manifest, version_id)
    data = net.get_json(entry["url"], mirror)
    data.setdefault("id", version_id)
    data["_manifest_entry"] = entry
    return data


PHASES = ("release", "snapshot", "pre", "rc", "old_beta", "old_alpha")
PHASE_LABELS = {
    "release": "正式版",
    "snapshot": "快照",
    "pre": "预发布",
    "rc": "候选版",
    "old_beta": "Beta",
    "old_alpha": "Alpha",
}


def version_phase(entry: dict) -> str:
    """把 Mojang 的 type（只有 release/snapshot/old_*）细分为发布阶段。

    官方清单里预发布和候选版都标成 snapshot，只能从版本号里区分：
    1.20.5-pre1 / 1.20.5-rc1 / 24w14a。
    """
    vtype = (entry.get("type") or "release").lower()
    if vtype == "release":
        return "release"
    if vtype in ("old_beta", "old_alpha"):
        return vtype
    version_id = str(entry.get("id", "")).lower()
    if "-rc" in version_id:
        return "rc"
    if "-pre" in version_id:
        return "pre"
    return "snapshot"


def select_versions(manifest: dict, kind: str = "release", search: str = "", limit: int = 0) -> list[dict]:
    items = manifest.get("versions", [])
    if kind != "all":
        items = [v for v in items if version_phase(v) == kind]
    if search:
        needle = search.lower()
        items = [v for v in items if needle in v["id"].lower()]
    return items[:limit] if limit else items


def latest_release(manifest: dict) -> str:
    return manifest["latest"]["release"]


# ---------------------------------------------------------------- 版本继承


def merge_versions(parent: dict, child: dict) -> dict:
    """把 Fabric/Forge 的子版本 JSON 合并到原版父版本上。"""
    merged = dict(parent)
    merged["id"] = child.get("id", parent.get("id"))
    merged["_parent_id"] = parent.get("id")
    for key in ("mainClass", "assetIndex", "assets", "javaVersion", "type", "releaseTime", "time"):
        if key in child:
            merged[key] = child[key]

    # 子版本库排在前面：同名库以子版本为准（Fabric 会用新版 ASM 等）
    libraries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for lib in list(child.get("libraries", [])) + list(parent.get("libraries", [])):
        key = _lib_key(lib)
        if key in seen:
            continue
        seen.add(key)
        libraries.append(lib)
    merged["libraries"] = libraries

    args = dict(parent.get("arguments") or {})
    child_args = child.get("arguments") or {}
    merged["arguments"] = {
        "game": list(child_args.get("game", [])) + list(args.get("game", [])),
        "jvm": list(child_args.get("jvm", [])) + list(args.get("jvm", [])),
    }
    if "minecraftArguments" in child:
        merged["minecraftArguments"] = child["minecraftArguments"]
    elif "minecraftArguments" in parent:
        merged["minecraftArguments"] = parent["minecraftArguments"]
    if "downloads" in parent:
        merged["downloads"] = parent["downloads"]
    return merged


def _lib_key(lib: dict) -> tuple[str, str]:
    parts = maven_coords(lib.get("name", ""))
    return (parts[0], parts[1]) if parts else ("", lib.get("name", ""))


def maven_coords(name: str) -> tuple[str, str, str, str | None, str]:
    """解析 group:artifact:version[:classifier][@ext]。"""
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    bits = name.split(":")
    if len(bits) < 3:
        return ("", "", "", None, ext)
    group, artifact, version = bits[0], bits[1], bits[2]
    classifier = bits[3] if len(bits) > 3 else None
    return group, artifact, version, classifier, ext


def maven_path(name: str) -> str:
    group, artifact, version, classifier, ext = maven_coords(name)
    if not group:
        return ""
    filename = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return "/".join([group.replace(".", "/"), artifact, version, filename])


# ---------------------------------------------------------------- 库解析


@dataclass
class LibraryFile:
    name: str
    path: str
    url: str
    sha1: str | None = None
    size: int | None = None
    is_native: bool = False
    extract_exclude: list[str] = field(default_factory=list)


def resolve_libraries(version: dict, platform: Platform) -> tuple[list[LibraryFile], list[LibraryFile]]:
    """返回 (普通库, 需要解压的原生库)。"""
    artifacts: list[LibraryFile] = []
    natives: list[LibraryFile] = []

    for lib in version.get("libraries", []):
        if not rules_allow(lib.get("rules"), platform):
            continue
        downloads = lib.get("downloads") or {}
        artifact = downloads.get("artifact")
        base_url = lib.get("url", "https://libraries.minecraft.net/")
        if not base_url.endswith("/"):
            base_url += "/"

        if artifact:
            artifacts.append(
                LibraryFile(
                    name=lib.get("name", ""),
                    path=artifact["path"],
                    url=artifact.get("url") or base_url + artifact["path"],
                    sha1=artifact.get("sha1"),
                    size=artifact.get("size"),
                )
            )
        elif "downloads" not in lib and lib.get("name"):
            # 老格式（1.12 及更早）没有 downloads 字段，按 Maven 约定拼地址；
            # 若 downloads 存在但只有 classifiers（纯原生库，如 jinput-platform），则跳过主 jar
            path = maven_path(lib["name"])
            if path:
                artifacts.append(
                    LibraryFile(
                        name=lib["name"],
                        path=path,
                        url=base_url + path,
                        sha1=lib.get("sha1"),
                        size=lib.get("size"),
                    )
                )

        native_key = (lib.get("natives") or {}).get(platform.os_name)
        if native_key:
            classifier = native_key.replace("${arch}", str(platform.bits))
            native_download = (downloads.get("classifiers") or {}).get(classifier)
            if native_download:
                natives.append(
                    LibraryFile(
                        name=f"{lib.get('name', '')}:{classifier}",
                        path=native_download["path"],
                        url=native_download.get("url")
                        or base_url + native_download["path"],
                        sha1=native_download.get("sha1"),
                        size=native_download.get("size"),
                        is_native=True,
                        extract_exclude=(lib.get("extract") or {}).get("exclude", []),
                    )
                )
    return artifacts, natives


def client_download(version: dict) -> dict | None:
    return (version.get("downloads") or {}).get("client")


def java_major(version: dict) -> int | None:
    info = version.get("javaVersion")
    if info:
        return info.get("majorVersion")
    return None


# ---------------------------------------------------------------- 本地版本


def version_dir(game_dir: Path, version_id: str) -> Path:
    return game_dir / "versions" / version_id


def save_version_json(game_dir: Path, version_id: str, data: dict) -> Path:
    target = version_dir(game_dir, version_id) / f"{version_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: v for k, v in data.items() if not k.startswith("_")}
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False, indent=2)
    return target


def load_local_version(game_dir: Path, version_id: str) -> dict | None:
    path = version_dir(game_dir, version_id) / f"{version_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_full_version(game_dir: Path, version_id: str) -> dict:
    """读取本地版本 JSON，并沿 inheritsFrom 合并父版本。"""
    child = load_local_version(game_dir, version_id)
    if child is None:
        raise FileNotFoundError(f"本地没有版本 {version_id}")
    if "inheritsFrom" in child:
        parent = load_full_version(game_dir, child["inheritsFrom"])
        return merge_versions(parent, child)
    return child
