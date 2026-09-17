"""Reject infrastructure failure masquerading as deliberate test-failure proof."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

project = sys.argv[1]
results = Path(__file__).resolve().parents[2] / "artifacts" / project
document = ET.parse(results / "junit.xml")
failures = document.findall(".//failure")
if not failures or not any("deliberate harness failure" in (node.text or "") for node in failures):
    raise SystemExit("Expected deliberate test failure missing from JUnit")
if document.findall(".//error") or document.findall(".//skipped"):
    raise SystemExit("Infrastructure errors or skipped scenarios invalidate failure proof")
for name in ("status.txt", "services.log", "policy.json"):
    if not (results / name).exists():
        raise SystemExit("Required sanitized diagnostic missing")
remaining = subprocess.run(["docker", "ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + project],
                           check=True, capture_output=True, text=True, timeout=10)
if remaining.stdout.strip():
    raise SystemExit("Disposable project containers survived cleanup")
print("Deliberate failure remained failure; diagnostics and project cleanup verified")
