# Hardware BIOS Baseline

Guardian can audit and apply a hardware baseline on supported **Linux** and **Windows** PCs from the device detail page. The goal is to reduce firmware-level bypass routes by setting a supervisor password, disabling external boot media, and enabling Secure Boot where the vendor interface allows it.

## Supported platforms

| Platform | OEM detection | BIOS interface | Vendors |
|----------|---------------|----------------|---------|
| Linux | SMBIOS via [`dmidecode`](https://lib.rs/crates/dmidecode) (`/sys/firmware/dmi/tables/`) | Kernel `firmware-attributes` sysfs | Dell, Lenovo, HP |
| Windows | WMI (`Win32_ComputerSystem`, `Win32_BIOS`) | Vendor CLIs staged on demand | Dell, HP, Lenovo, Surface |
| Android | — | — | Unsupported |

### Linux sysfs paths

| Vendor | Kernel module | Sysfs root |
|--------|---------------|------------|
| Dell | `dell-wmi-sysman` | `/sys/class/firmware-attributes/dell-wmi-sysman/` |
| Lenovo | `think-lmi` (`modprobe think-lmi`) | `/sys/class/firmware-attributes/thinklmi/` |
| HP | `hp-bioscfg` | `/sys/class/firmware-attributes/hp-bioscfg/` or `/sys/devices/platform/hp-bioscfg/` |

The agent uses the shared [kernel firmware-attributes ABI](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-class-firmware-attributes) for authentication sessions and attribute reads/writes.

### Windows payload staging

Windows agents download signed vendor payload archives from:

`GET /api/agent/bios-payloads/<vendor>`

Payload manifests live in `server/static/bios-payloads/manifest.json`. Production admins supply licensed vendor binaries (Dell CCTK, HP CMSL, Lenovo WMI helpers, Surface SEMM) as ZIP archives under `server/static/bios-payloads/<vendor>/`.

## Admin workflow

1. Open **Devices → device detail → Overview**.
2. Use **Audit settings** for a read-only compliance receipt.
3. Use **Apply hardware baseline** to set the supervisor password and attempt USB boot / Secure Boot enforcement.
4. Use **Reveal supervisor password** if you need the escrowed password stored on the Guardian server.

Apply and audit require the agent to be online. Actions are manual only; nothing runs automatically on device approval. **Apply**, **audit**, and **reveal supervisor password** require `can_manage_policies` on a child mapped to the device (or household admin).

## Password escrow

- The agent generates a random supervisor password in memory only.
- The password is returned to the server over the existing WSS session and Fernet-encrypted into `agent_device.bios_supervisor_password_escrow`.
- The agent does not write the password to local disk.
- Password reveal is logged server-side.

## Compliance reporting

The server stores a structured receipt in `hardware_compliance_json` and a summary status in `hardware_compliance_status`:

- `compliant`
- `non_compliant`
- `unknown`
- `unsupported`
- `pending`

Non-compliant Windows/Linux devices appear in the device protection summary and admin device list with a **Hardware non-compliant** badge.

## Failure modes

| Situation | Behaviour |
|-----------|-----------|
| Existing unknown BIOS password | Password change skipped; device marked non-compliant |
| Missing Linux kernel module | OEM detected but interface unavailable; actionable error in receipt |
| HP certificate-based auth (SPM) | Password-based apply not supported; receipt explains limitation |
| Lenovo password + settings same boot cycle | Password may apply first; remaining settings may require reboot |
| Windows payload not staged | Audit reports missing vendor tools; apply fails until payload is downloaded |

## Agent commands

| Action | Purpose |
|--------|---------|
| `detect_hardware_oem` | Return OEM, model, interface, supported flag |
| `audit_hardware_baseline` | Read-only compliance receipt |
| `apply_hardware_baseline` | Apply baseline and return receipt + escrow password |

## Related

- [Windows agent](../platforms/windows-agent.md)
- [Pairing & approval](../workflows/pairing-and-approval.md)
