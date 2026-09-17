"""Local consistency checks, not a substitute for SuiteCloud account validation."""
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parents[1]
netsuite = root / "netsuite"
for path in netsuite.rglob("*.xml"):
    element = ET.parse(path)
    for node in element.iter():
        if node.tag == "scriptfile":
            target = netsuite / "FileCabinet" / node.text.strip("[]/")
            assert target.is_file(), target
        scriptid = node.get("scriptid")
        if scriptid:
            assert len(scriptid) <= 40, scriptid
for path in (netsuite / "FileCabinet").rglob("*.js"):
    # NetSuite's tag parser needs the annotation value on its own line.
    # JavaScript syntax validation alone cannot catch a malformed SuiteScript header.
    source = path.read_text()
    assert re.search(r"(?m)^\s*\*\s+@NApiVersion\s+(?:2\.1|2\.0|2\.[xX])\s*$", source), \
        f"Invalid or inline @NApiVersion header: {path}"
    subprocess.run(["node", "--check", str(path)], check=True)
html = (netsuite / "FileCabinet/SuiteScripts/netsuite-agent/chat.html").read_text()
script = re.search(r"<script>([\s\S]*?)</script>", html).group(1)
subprocess.run(["node", "--check"], input=script, text=True, check=True)
print("SDF XML parses; file references and JavaScript syntax pass. Account validation remains required.")
