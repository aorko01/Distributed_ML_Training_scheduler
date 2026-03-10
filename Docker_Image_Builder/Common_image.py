import os
import docker
import shutil
from pathlib import Path

DOCKER_USER = os.environ["DOCKER_HUB_USERNAME"]
DOCKER_PASS = os.environ["DOCKER_HUB_PASSWORD"]

BASE_IMAGES = {
    "ml-base": [
        "numpy",
        "scipy",
        "pandas",
        "tqdm",
        "pyyaml"
    ],

    "ml-base-transformers": [
        "numpy",
        "scipy",
        "pandas",
        "tqdm",
        "pyyaml",
        "transformers",
        "datasets",
        "accelerate",
        "tokenizers",
        "sentencepiece",
        "peft",
        "huggingface-hub"
    ],

    "ml-base-vision": [
        "numpy",
        "scipy",
        "pandas",
        "tqdm",
        "pyyaml",
        "opencv-python",
        "albumentations",
        "Pillow",
        "scikit-learn"
    ],

    "ml-base-training": [
        "numpy",
        "scipy",
        "pandas",
        "tqdm",
        "pyyaml",
        "tensorboard",
        "wandb",
        "hydra-core",
        "omegaconf"
    ],
}

BASE_DOCKER_IMAGE = "pytorch/pytorch:2.1.0-cuda11.8-cudnn8-runtime"


def create_dockerfile(packages):
    lines = [
        f"FROM {BASE_DOCKER_IMAGE}",
        "",
        "WORKDIR /workspace",
        "",
        "RUN pip install --no-cache-dir \\"
    ]

    for i, pkg in enumerate(packages):
        if i == len(packages) - 1:
            lines.append(f"    {pkg}")
        else:
            lines.append(f"    {pkg} \\")

    lines.append("")
    lines.append('CMD ["python"]')

    return "\n".join(lines)


def build_and_push(client, name, packages):
    tag = f"{DOCKER_USER}/{name}:latest"

    build_dir = Path(f"./build_{name}")
    build_dir.mkdir(exist_ok=True)

    dockerfile = create_dockerfile(packages)

    with open(build_dir / "Dockerfile", "w") as f:
        f.write(dockerfile)

    print(f"\nBuilding {tag}")

    image, logs = client.images.build(
        path=str(build_dir),
        tag=tag,
        rm=True,
        forcerm=True,
    )

    for chunk in logs:
        if "stream" in chunk:
            print(chunk["stream"].strip())

    print(f"Pushing {tag}")

    for line in client.images.push(
        repository=f"{DOCKER_USER}/{name}",
        tag="latest",
        stream=True,
        decode=True,
    ):
        if "status" in line:
            print(line["status"])

    print(f"Finished {tag}")


def main():
    client = docker.from_env()

    print("Logging into Docker Hub...")
    client.login(username=DOCKER_USER, password=DOCKER_PASS)

    build_dirs = []

    for name, packages in BASE_IMAGES.items():
        build_dirs.append(Path(f"./build_{name}"))
        build_and_push(client, name, packages)

    # Cleanup build directories
    for build_dir in build_dirs:
        if build_dir.exists():
            shutil.rmtree(build_dir)
            print(f"Deleted build directory: {build_dir}")

    print("\nAll base images built, pushed, and temporary directories removed.")


if __name__ == "__main__":
    main()