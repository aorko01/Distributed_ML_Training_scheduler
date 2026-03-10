import os
import docker

DOCKER_USER = os.environ["DOCKER_HUB_USERNAME"]

IMAGES = [
    "ml-base",
    "ml-base-transformers",
    "ml-base-vision",
    "ml-base-training"
]


def main():
    client = docker.from_env()

    print("Pre-pulling ML base images...")

    for img in IMAGES:
        full = f"{DOCKER_USER}/{img}:latest"

        print(f"\nPulling {full}")

        for line in client.api.pull(full, stream=True, decode=True):
            if "status" in line:
                print(line["status"])

    print("\nAll images cached locally.")


if __name__ == "__main__":
    main()