"""Generate constrained SDF XML; never execute model-provided code or shell commands."""
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

log = logging.getLogger(__name__)


class CustomField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str = Field(pattern=r"^custrecord_[a-z][a-z0-9_]{0,28}$")
    label: str = Field(min_length=1, max_length=100)
    field_type: Literal["TEXT", "TEXTAREA", "CLOBTEXT", "INTEGER", "FLOAT", "CURRENCY", "CHECKBOX", "DATE"]
    mandatory: bool = False


class CustomType(BaseModel):
    model_config = ConfigDict(extra="forbid")
    script_id: str = Field(pattern=r"^customrecord_[a-z][a-z0-9_]{0,26}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    include_name: bool = True
    fields: list[CustomField] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_fields(self):
        ids = [f.script_id for f in self.fields]
        if len(set(ids)) != len(ids):
            raise ValueError("Custom field script IDs must be unique")
        if self.script_id.startswith("customrecord_nsa_") or any(i.startswith("custrecord_nsa_") for i in ids):
            raise ValueError("Agent storage identifiers are reserved")
        return self


def xml_for(spec: CustomType, entry_roles=None):
    root = ET.Element("customrecordtype", scriptid=spec.script_id)
    for key, value in {"recordname": spec.name, "description": spec.description,
                       "includename": "T" if spec.include_name else "F", "accesstype": "USEPERMISSIONLIST",
                       "allowuiaccess": "T", "enableoptimisticlocking": "T"}.items():
        ET.SubElement(root, key).text = value
    permissions = ET.SubElement(root, "permissions")
    roles = ["ADMINISTRATOR"]
    for role in entry_roles or []:
        text = str(role or "").strip()
        if not text or text.upper() == "ADMINISTRATOR":
            continue
        # SDF permittedrole accepts standard role IDs or [scriptid=customrole_...].
        # Bare numeric internal IDs and accountspecificvalue refs are rejected by validate.
        lower = text.lower()
        if lower.startswith("[scriptid=") and lower.endswith("]"):
            ref = text
        elif re.fullmatch(r"customrole_[a-z][a-z0-9_]*", lower):
            ref = f"[scriptid={lower}]"
        else:
            continue
        if ref not in roles and ref.upper() != "ADMINISTRATOR":
            roles.append(ref)
    for role in roles:
        permission = ET.SubElement(permissions, "permission")
        ET.SubElement(permission, "permittedrole").text = role
        ET.SubElement(permission, "permittedlevel").text = "FULL"
    if spec.fields:
        fields = ET.SubElement(root, "customrecordcustomfields")
        for field in spec.fields:
            node = ET.SubElement(fields, "customrecordcustomfield", scriptid=field.script_id)
            for key, value in {"label": field.label, "fieldtype": field.field_type, "storevalue": "T",
                               "ismandatory": "T" if field.mandatory else "F"}.items():
                ET.SubElement(node, key).text = value
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


class SDFExecutor:
    def __init__(self, settings):
        self.settings = settings

    def folder(self, job):
        if not re.fullmatch(r"[0-9]+", str(job)):
            raise ValueError("Invalid deployment job ID")
        return self.settings.data_dir.resolve() / "creation-deployments" / str(job)

    def files(self, spec, entry_roles=None):
        if not self.settings.sdf_auth_id or not self.settings.account:
            raise ValueError("Configure AGENT_SDF_AUTH_ID for the same NetSuite account before preparing a custom type")
        return {
            f"Objects/{spec.script_id}.xml": xml_for(spec, entry_roles=entry_roles),
            "manifest.xml": '<manifest projecttype="ACCOUNTCUSTOMIZATION"><projectname>Agent approved customization</projectname><frameworkversion>1.0</frameworkversion><dependencies><features><feature required="true">CUSTOMRECORDS</feature></features></dependencies></manifest>\n',
            "deploy.xml": f'<deploy><objects><path>~/Objects/{spec.script_id}.xml</path></objects></deploy>\n',
            "project.json": json.dumps({"defaultAuthId": self.settings.sdf_auth_id}) + "\n",
            "suitecloud.config.js": "module.exports = {defaultProjectFolder: '.'};\n",
        }

    @staticmethod
    def digest(files):
        return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()

    async def prepare(self, job, spec, entry_roles=None):
        """Write the isolated project and return a reviewable draft without remote SDF.

        SuiteCloud validate/deploy often hang on broken OS secure storage. Draft review
        only needs local XML + a NetSuite availability check (done by the caller).
        Remote validate runs once on approve, with an auth preflight and a hard timeout.
        """
        roles = list(entry_roles or [])
        files = self.files(spec, entry_roles=roles)
        folder = self.folder(job)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, content in files.items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_text(content)
            target.chmod(0o600)
        return {"digest": self.digest(files), "xml": xml_for(spec, entry_roles=roles),
                "entry_roles": roles,
                "validation": "Local artifact prepared. SuiteCloud server validation runs after approval."}

    async def verify(self, job, spec, digest, entry_roles=None):
        roles = list(entry_roles or [])
        expected = self.files(spec, entry_roles=roles)
        if self.digest(expected) != digest:
            raise ValueError("Deployment configuration changed; prepare and approve a new draft")
        root = self.folder(job)
        for name, text in expected.items():
            path = root / name
            if path.is_symlink() or not path.is_file() or path.read_text() != text:
                raise ValueError("Deployment artifact changed or is missing; prepare a new draft")
        await self.ensure_auth()
        await self.run(job, "project:validate")

    def suitecloud_env(self):
        """CI credentials for SuiteCloud 2025+ PKCS#12 auth (avoids OS keyring hangs)."""
        env = {**os.environ, "CI": "true", "SUITECLOUD_CI": "1"}
        passkey = getattr(self.settings, "sdf_ci_passkey", "") or ""
        if not passkey:
            path = Path("secrets/sdf-ci-passkey")
            if path.is_file():
                passkey = path.read_text().strip()
        if passkey:
            env["SUITECLOUD_CI_PASSKEY"] = passkey
        return env

    async def ensure_auth(self):
        """Fail fast when SuiteCloud auth/secure storage cannot be used."""
        if not self.settings.sdf_auth_id:
            raise ValueError("AGENT_SDF_AUTH_ID is not configured")
        timeout = min(30, self.settings.sdf_timeout)
        logpath = self.settings.data_dir.resolve() / "creation-deployments" / "auth-preflight.log"
        logpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as output:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.settings.suitecloud_bin, "account:manageauth", "--info", self.settings.sdf_auth_id,
                    stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True, env=self.suitecloud_env())
            except FileNotFoundError as exc:
                raise ValueError("SuiteCloud CLI is not installed or AGENT_SUITECLOUD_BIN is incorrect") from exc
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except (TimeoutError, asyncio.CancelledError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise ValueError(
                    f"SuiteCloud auth preflight timed out after {timeout}s while reading auth ID "
                    f"'{self.settings.sdf_auth_id}'. Secure storage is likely blocked or the CLI is hung. "
                    "Run `suitecloud account:manageauth --list` as the same OS user that runs the worker, "
                    "fix keyring/libsecret access, recreate the CI auth with account:setup:ci, then retry. "
                    f"See {logpath}."
                ) from None
        text = logpath.read_text(errors="replace")[-20000:]
        if process.returncode != 0 or re.search(r"secure storage is inaccessible", text, re.I):
            raise ValueError(
                "SuiteCloud cannot read SDF authentication (secure storage inaccessible or auth ID missing). "
                f"Auth ID '{self.settings.sdf_auth_id}' must be usable by the worker OS user. "
                "Set SUITECLOUD_CI=1 and SUITECLOUD_CI_PASSKEY (or secrets/sdf-ci-passkey), then recreate "
                f"CI auth with account:setup:ci. Details: {logpath}"
            )
        if self.settings.sdf_auth_id.lower() not in text.lower() and "account" not in text.lower():
            if not text.strip():
                raise ValueError(f"SuiteCloud auth preflight returned no details for '{self.settings.sdf_auth_id}'. See {logpath}")

    async def run(self, job, command):
        if command not in {"project:validate", "project:deploy"}:
            raise ValueError("Unsupported deployment operation")
        folder = self.folder(job)
        logpath = folder / (command.replace(":", "-") + ".log")
        log.info("Starting SuiteCloud %s for job %s (timeout %ss)", command, job, self.settings.sdf_timeout)
        fd = os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as output:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.settings.suitecloud_bin, command, cwd=folder,
                    stdin=asyncio.subprocess.DEVNULL, stdout=output, stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True, env=self.suitecloud_env())
            except FileNotFoundError as exc:
                raise ValueError("SuiteCloud CLI is not installed or AGENT_SUITECLOUD_BIN is incorrect") from exc
            try:
                await asyncio.wait_for(process.wait(), timeout=self.settings.sdf_timeout)
            except (TimeoutError, asyncio.CancelledError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise ValueError(
                    f"SuiteCloud {command} timed out after {self.settings.sdf_timeout}s. "
                    "This usually means auth/secure storage is hanging (manageauth). "
                    "Kill leftover suitecloud/java processes, run account:manageauth --list as the worker user, "
                    f"then retry. Log: {logpath}"
                ) from None
        text = logpath.read_text(errors="replace")[-100000:]
        if re.search(r"secure storage is inaccessible", text, re.I):
            raise ValueError(
                f"SuiteCloud {command} failed because secure storage is inaccessible. "
                "Fix keyring/libsecret for the worker OS user and recreate AGENT_SDF_AUTH_ID with account:setup:ci. "
                f"Log: {logpath}"
            )
        account = re.search(r"Account ID:\s*([A-Za-z0-9_-]+)", text)
        if not account or account[1].upper() != self.settings.account.upper():
            raise ValueError("SDF target account could not be verified. Check the deployment auth ID and local log.")
        if process.returncode != 0 or not re.search(r"Status:\s*SUCCESS\b", text):
            details = [m.strip() for m in re.findall(r"Details:\s*(.+)", text)]
            errors = [m.strip() for m in re.findall(r"^\s*ERROR:\s*(.+)", text, re.M)]
            summary = "; ".join((details or errors)[:3]) or f"see {logpath}"
            # Detect partial creates: type succeeded but a field collided.
            type_created = bool(re.search(
                r"✔\s+Step \d+: CUSTOM_OBJECT_CREATION --- (customrecord_[a-z0-9_]+) \(customrecordtype\)",
                text))
            already = re.search(r"already exists", text, re.I)
            hint = ""
            if already and "custrecord_" in text.lower():
                hint = (
                    " Custom field script IDs are account-global; choose unique "
                    "custrecord_... IDs (do not reuse fields from another type)."
                )
            elif type_created:
                hint = " The custom record type may already exist in NetSuite; reconcile before retrying."
            raise ValueError(
                f"SDF {command} failed: {summary}.{hint} Log: {logpath}"
            )
        log.info("SuiteCloud %s succeeded for job %s", command, job)

    async def deploy(self, job, *, ensure_auth=True):
        if ensure_auth:
            await self.ensure_auth()
        await self.run(job, "project:deploy")
