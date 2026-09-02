import re

import requests
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["docker"])

DOCKER_HUB_TAGS_URL = "https://hub.docker.com/v2/repositories/pytorch/pytorch/tags/"

RUNTIME_TAG_RE = re.compile(
    r"^(?P<pytorch>[\d.]+)-cuda(?P<cuda>[\d.]+)-cudnn(?P<cudnn>[\d.]+)-runtime$"
)


def _sort_key(version: str) -> tuple:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return tuple(0 for _ in version.split("."))


def _fetch_all_tags() -> list[str]:
    tags: list[str] = []
    url = f"{DOCKER_HUB_TAGS_URL}?page_size=100"
    while url:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        tags.extend(tag["name"] for tag in payload.get("results", []))
        url = payload.get("next")
    return tags


def _parse_runtime_tags(tags: list[str]) -> list[dict]:
    versions: dict[str, list[dict]] = {}
    for name in tags:
        match = RUNTIME_TAG_RE.match(name)
        if not match:
            continue
        pytorch = match.group("pytorch")
        cuda = match.group("cuda")
        cudnn = match.group("cudnn")
        versions.setdefault(pytorch, []).append(
            {
                "cuda": cuda,
                "cudnn": cudnn,
                "tag": f"pytorch/pytorch:{name}",
            }
        )

    result = []
    for pytorch, variants in sorted(
        versions.items(), key=lambda item: _sort_key(item[0]), reverse=True
    ):
        variants.sort(
            key=lambda v: (_sort_key(v["cuda"]), _sort_key(v["cudnn"])),
            reverse=True,
        )
        result.append({"version": pytorch, "cudaVersions": variants})

    return result


@router.get("/pytorch-tags")
def get_pytorch_tags():
    try:
        tags = _fetch_all_tags()
        return _parse_runtime_tags(tags)
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch tags from Docker Hub: {e}"
        )
