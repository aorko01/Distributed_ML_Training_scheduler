import importlib.util
from pathlib import Path


def test_artifact_redaction_removes_headers_tickets_and_enrollment_keys():
    path = Path(__file__).resolve().parents[3] / "test/interactive_e2e/run.py"
    spec = importlib.util.spec_from_file_location("interactive_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    credential = "role-" + "s" * 40
    ticket = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ1c2VyLWEifQ.signature123"
    key = "a" * 64
    result = module.safe_logs("Authorization: Bearer " + credential + " ticket=" + ticket + " key=" + key, [credential])
    assert credential not in result and ticket not in result and key not in result
