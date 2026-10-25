"""Render a host-bound Task Scheduler definition without registering or running it."""

import argparse
from datetime import datetime
from pathlib import Path
import re
import xml.etree.ElementTree as ET


NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)


def render(user_sid: str, distribution: str, start_boundary: str, linux_user: str) -> bytes:
    if not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", user_sid):
        raise ValueError("Expected the original distribution owner's Windows user SID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", distribution):
        raise ValueError("Expected an explicitly verified distribution name")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", linux_user) or linux_user in {"root", "nobody"}:
        raise ValueError("Expected the original non-root Linux login user")
    boundary = datetime.fromisoformat(start_boundary)
    if boundary.tzinfo is not None:
        raise ValueError("Start boundary must use the Windows host's local time")
    root = ET.parse(Path(__file__).with_name("pcr-relay-wsl-guard.xml.example")).getroot()
    values = {
        "__WINDOWS_USER_SID__": user_sid,
        "__START_BOUNDARY__": boundary.isoformat(timespec="seconds"),
    }
    for node in root.iter():
        if node.text in values:
            node.text = values[node.text]
    root.find(f"{{{NS}}}Actions/{{{NS}}}Exec/{{{NS}}}Arguments").text = (
        f"--distribution {distribution} --user {linux_user} --cd / --exec /usr/bin/sleep infinity"
    )
    if any(node.text and "__" in node.text for node in root.iter()):
        raise ValueError("Task definition contains an unresolved placeholder")
    # Task Scheduler consumes a Unicode BSTR; an explicit UTF-8 declaration
    # conflicts with that representation when read by Windows PowerShell.
    return ET.tostring(root, encoding="utf-16", xml_declaration=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-sid", required=True)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--start-boundary", required=True)
    parser.add_argument("--linux-user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = render(args.user_sid, args.distribution, args.start_boundary, args.linux_user)
    with args.output.open("xb") as output:
        output.write(payload)
    print(args.output)
