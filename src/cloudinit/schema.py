"""Cloud-init node editor schema: one node owns each cloud-config key."""

from __future__ import annotations

NODES: list[dict] = [
    {
        "id": "hostname",
        "group": "Identity",
        "label": "Hostname",
        "help": "Sets the guest hostname and FQDN. {{hostname}} uses the machine hostname field.",
        "fields": [
            {"key": "hostname", "label": "Short hostname", "type": "string"},
            {"key": "fqdn", "label": "FQDN", "type": "string"},
            {"key": "prefer_fqdn_over_hostname", "label": "Prefer FQDN", "type": "bool"},
            {"key": "create_hostname_file", "label": "Create /etc/hostname", "type": "bool"},
            {"key": "preserve_hostname", "label": "Preserve existing hostname", "type": "bool"},
        ],
    },
    {
        "id": "timezone",
        "group": "Identity",
        "label": "Timezone",
        "help": "IANA timezone for the guest. {{timezone}} uses the machine timezone field.",
        "fields": [
            {"key": "timezone", "label": "Timezone", "type": "string"},
        ],
    },
    {
        "id": "locale",
        "group": "Identity",
        "label": "Locale",
        "help": "System locale and optional locale config file path.",
        "fields": [
            {"key": "locale", "label": "Locale", "type": "string"},
            {"key": "locale_configfile", "label": "Locale config file", "type": "string"},
        ],
    },
    {
        "id": "users",
        "group": "Users and SSH",
        "label": "Users",
        "help": "Cloud-init user entries. Rows without a name are dropped on save.",
        "fields": [
            {
                "key": "users",
                "label": "Users",
                "type": "row_list",
                "item_fields": [
                    {"key": "name", "label": "Name", "type": "string", "required": True},
                    {"key": "gecos", "label": "GECOS", "type": "string"},
                    {"key": "homedir", "label": "Home directory", "type": "string"},
                    {"key": "shell", "label": "Shell", "type": "string"},
                    {"key": "primary_group", "label": "Primary group", "type": "string"},
                    {"key": "sudo", "label": "Sudo rule", "type": "string"},
                    {"key": "groups", "label": "Groups (comma-separated)", "type": "string"},
                    {"key": "lock_passwd", "label": "Lock password", "type": "bool"},
                    {"key": "system", "label": "System user", "type": "bool"},
                    {"key": "uid", "label": "UID", "type": "string"},
                    {"key": "hashed_passwd", "label": "Hashed password", "type": "string"},
                    {
                        "key": "ssh_authorized_keys",
                        "label": "SSH authorized keys",
                        "type": "string_list",
                        "block_token": "ssh_keys",
                    },
                ],
            },
        ],
    },
    {
        "id": "default_user",
        "group": "Users and SSH",
        "label": "Default user",
        "help": "Override the default user object (cloud-init user key).",
        "fields": [
            {
                "key": "user",
                "label": "Default user",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "groups",
        "group": "Users and SSH",
        "label": "Groups",
        "help": "Group entries. Lines like sudo: alice, bob become mappings; plain lines stay strings.",
        "fields": [
            {"key": "groups", "label": "Groups", "type": "lines", "parser": "groups"},
        ],
    },
    {
        "id": "passwords",
        "group": "Users and SSH",
        "label": "Passwords",
        "help": "Password and chpasswd settings. Use {{password}} or {{password_hash}} placeholders.",
        "fields": [
            {"key": "password", "label": "Password module", "type": "string"},
            {
                "key": "chpasswd",
                "label": "Chpasswd",
                "type": "object",
                "object_fields": [
                    {"key": "expire", "label": "Expire passwords", "type": "bool"},
                    {
                        "key": "users",
                        "label": "Users",
                        "type": "row_list",
                        "item_fields": [
                            {"key": "name", "label": "Name", "type": "string"},
                            {"key": "password", "label": "Password", "type": "string"},
                            {
                                "key": "type",
                                "label": "Type",
                                "type": "enum",
                                "choices": ["", "hash", "text", "RANDOM"],
                            },
                        ],
                    },
                ],
            },
        ],
    },
    {
        "id": "ssh",
        "group": "Users and SSH",
        "label": "SSH",
        "help": "SSH module settings. Host keys (ssh_keys) are separate from authorized keys.",
        "fields": [
            {
                "key": "ssh_authorized_keys",
                "label": "Authorized keys",
                "type": "string_list",
                "block_token": "ssh_keys",
            },
            {"key": "disable_root", "label": "Disable root login", "type": "bool"},
            {"key": "disable_root_opts", "label": "Disable root options", "type": "string"},
            {"key": "ssh_deletekeys", "label": "Delete existing keys", "type": "bool"},
            {"key": "ssh_genkeytypes", "label": "Generate key types", "type": "string_list"},
            {"key": "ssh_quiet_keygen", "label": "Quiet keygen", "type": "bool"},
            {"key": "allow_public_ssh_keys", "label": "Allow public SSH keys", "type": "bool"},
            {"key": "ssh_pwauth", "label": "SSH password authentication", "type": "bool"},
            {"key": "no_ssh_fingerprints", "label": "No SSH fingerprints", "type": "bool"},
            {"key": "emit_keys_to_console", "label": "Emit keys to console", "type": "bool"},
            {
                "key": "__yaml_ssh__",
                "label": "Host keys",
                "type": "yaml",
                "yaml_mode": "mapping",
                "yaml_keys": ["ssh_keys", "ssh_publish_hostkeys"],
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "ssh_import",
        "group": "Users and SSH",
        "label": "SSH import",
        "help": "Import SSH keys from external sources.",
        "fields": [
            {"key": "ssh_import_id", "label": "SSH import IDs", "type": "string_list"},
        ],
    },
    {
        "id": "packages",
        "group": "Packages",
        "label": "Packages",
        "help": "Package lists and apt settings. {{packages}} uses the machine packages field.",
        "fields": [
            {"key": "packages", "label": "Packages", "type": "string_list", "block_token": "packages"},
            {"key": "package_update", "label": "Package update", "type": "bool"},
            {"key": "package_upgrade", "label": "Package upgrade", "type": "bool"},
            {"key": "package_reboot_if_required", "label": "Reboot if required", "type": "bool"},
        ],
    },
    {
        "id": "write_files",
        "group": "Files and commands",
        "label": "Write files",
        "help": "Files written during cloud-init. A row without a path is dropped.",
        "fields": [
            {
                "key": "write_files",
                "label": "Files",
                "type": "row_list",
                "item_fields": [
                    {"key": "path", "label": "Path", "type": "string", "required": True},
                    {"key": "content", "label": "Content", "type": "text"},
                    {"key": "owner", "label": "Owner", "type": "string"},
                    {"key": "permissions", "label": "Permissions", "type": "string"},
                    {"key": "append", "label": "Append", "type": "bool"},
                    {"key": "defer", "label": "Defer", "type": "bool"},
                    {
                        "key": "encoding",
                        "label": "Encoding",
                        "type": "enum",
                        "choices": ["", "text", "b64", "base64", "gzip", "gz+base64"],
                    },
                ],
            },
        ],
    },
    {
        "id": "runcmd",
        "group": "Files and commands",
        "label": "Run commands",
        "help": "Commands run once at the end of cloud-init.",
        "fields": [
            {"key": "runcmd", "label": "Commands", "type": "lines"},
        ],
    },
    {
        "id": "bootcmd",
        "group": "Files and commands",
        "label": "Boot commands",
        "help": "Commands run very early on every boot.",
        "fields": [
            {"key": "bootcmd", "label": "Commands", "type": "lines"},
        ],
    },
    {
        "id": "final_message",
        "group": "Files and commands",
        "label": "Final message",
        "help": "Message printed when cloud-init finishes.",
        "fields": [
            {"key": "final_message", "label": "Message", "type": "text"},
        ],
    },
    {
        "id": "growpart",
        "group": "Disks",
        "label": "Growpart",
        "help": "Grow partitions to fill available space.",
        "fields": [
            {
                "key": "growpart",
                "label": "Growpart",
                "type": "object",
                "object_fields": [
                    {
                        "key": "mode",
                        "label": "Mode",
                        "type": "enum",
                        "choices": ["", "auto", "growpart", "off", "false"],
                    },
                    {"key": "devices", "label": "Devices", "type": "string_list"},
                    {"key": "ignore_growroot_disabled", "label": "Ignore growroot disabled", "type": "bool"},
                ],
            },
        ],
    },
    {
        "id": "resize_rootfs",
        "group": "Disks",
        "label": "Resize rootfs",
        "help": "Resize the root filesystem.",
        "fields": [
            {
                "key": "resize_rootfs",
                "label": "Resize rootfs",
                "type": "enum",
                "choices": ["", "true", "false", "noblock"],
            },
        ],
    },
    {
        "id": "disk_setup",
        "group": "Disks",
        "label": "Disk setup",
        "help": "Disk setup module (YAML mapping).",
        "fields": [
            {
                "key": "disk_setup",
                "label": "Disk setup",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "fs_setup",
        "group": "Disks",
        "label": "Filesystems",
        "help": "Filesystem setup module (YAML list).",
        "fields": [
            {
                "key": "fs_setup",
                "label": "Filesystems",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "list",
            },
        ],
    },
    {
        "id": "mounts",
        "group": "Disks",
        "label": "Mounts",
        "help": "Mount points module (YAML list).",
        "fields": [
            {
                "key": "mounts",
                "label": "Mounts",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "list",
            },
        ],
    },
    {
        "id": "hosts",
        "group": "Network",
        "label": "Hosts",
        "help": "Manage /etc/hosts behavior.",
        "fields": [
            {
                "key": "manage_etc_hosts",
                "label": "Manage /etc/hosts",
                "type": "enum",
                "choices": ["", "true", "false", "template", "localhost"],
            },
        ],
    },
    {
        "id": "resolv_conf",
        "group": "Network",
        "label": "Resolv.conf",
        "help": "DNS resolver configuration.",
        "fields": [
            {"key": "manage_resolv_conf", "label": "Manage resolv.conf", "type": "bool"},
            {
                "key": "resolv_conf",
                "label": "Resolv.conf",
                "type": "object",
                "object_fields": [
                    {"key": "nameservers", "label": "Nameservers", "type": "string_list"},
                    {"key": "searchdomains", "label": "Search domains", "type": "string_list"},
                    {"key": "domain", "label": "Domain", "type": "string"},
                    {"key": "options", "label": "Options (key:value per line)", "type": "lines", "parser": "options"},
                    {"key": "sortlist", "label": "Sortlist", "type": "string_list"},
                ],
            },
        ],
    },
    {
        "id": "ntp",
        "group": "Network",
        "label": "NTP",
        "help": "NTP client configuration.",
        "fields": [
            {
                "key": "ntp",
                "label": "NTP",
                "type": "object",
                "object_fields": [
                    {"key": "enabled", "label": "Enabled", "type": "bool"},
                    {"key": "ntp_client", "label": "NTP client", "type": "string"},
                    {"key": "servers", "label": "Servers", "type": "string_list"},
                    {"key": "pools", "label": "Pools", "type": "string_list"},
                    {"key": "peers", "label": "Peers", "type": "string_list"},
                    {"key": "allow", "label": "Allow", "type": "string_list"},
                ],
            },
        ],
    },
    {
        "id": "phone_home",
        "group": "Power and phone home",
        "label": "Phone home",
        "help": "Post install status to a URL.",
        "fields": [
            {
                "key": "phone_home",
                "label": "Phone home",
                "type": "object",
                "object_fields": [
                    {"key": "url", "label": "URL", "type": "string"},
                    {"key": "post", "label": "POST fields", "type": "all_or_list"},
                    {"key": "tries", "label": "Tries", "type": "int"},
                ],
            },
        ],
    },
    {
        "id": "power_state",
        "group": "Power and phone home",
        "label": "Power state",
        "help": "Power off or reboot after cloud-init.",
        "fields": [
            {
                "key": "power_state",
                "label": "Power state",
                "type": "object",
                "object_fields": [
                    {
                        "key": "mode",
                        "label": "Mode",
                        "type": "enum",
                        "choices": ["", "poweroff", "halt", "reboot"],
                    },
                    {"key": "message", "label": "Message", "type": "string"},
                    {"key": "timeout", "label": "Timeout", "type": "int"},
                    {"key": "condition", "label": "Condition", "type": "string"},
                    {"key": "delay", "label": "Delay", "type": "flex"},
                ],
            },
        ],
    },
    {
        "id": "keyboard",
        "group": "Identity",
        "label": "Keyboard",
        "help": "Keyboard layout module.",
        "fields": [
            {
                "key": "keyboard",
                "label": "Keyboard",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "apt",
        "group": "Packages",
        "label": "Apt",
        "help": "Apt configuration (apt and apt_pipelining keys).",
        "fields": [
            {
                "key": "__yaml_apt__",
                "label": "Apt",
                "type": "yaml",
                "yaml_mode": "mapping",
                "yaml_keys": ["apt", "apt_pipelining"],
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "snap",
        "group": "Packages",
        "label": "Snap",
        "help": "Snap package module.",
        "fields": [
            {"key": "snap", "label": "Snap", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "yum_repos",
        "group": "Packages",
        "label": "Yum repos",
        "help": "Yum repository configuration.",
        "fields": [
            {"key": "yum_repos", "label": "Yum repos", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "zypper_repos",
        "group": "Packages",
        "label": "Zypper",
        "help": "Zypper repository configuration.",
        "fields": [
            {"key": "zypper_repos", "label": "Zypper repos", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "apk_repos",
        "group": "Packages",
        "label": "APK",
        "help": "APK repository configuration.",
        "fields": [
            {"key": "apk_repos", "label": "APK repos", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "ca_certs",
        "group": "Agents and distro",
        "label": "CA certificates",
        "help": "CA certificate module.",
        "fields": [
            {"key": "ca_certs", "label": "CA certificates", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "rsyslog",
        "group": "Agents and distro",
        "label": "Rsyslog",
        "help": "Rsyslog configuration.",
        "fields": [
            {"key": "rsyslog", "label": "Rsyslog", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "ansible",
        "group": "Agents and distro",
        "label": "Ansible",
        "help": "Ansible module.",
        "fields": [
            {"key": "ansible", "label": "Ansible", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "puppet",
        "group": "Agents and distro",
        "label": "Puppet",
        "help": "Puppet module.",
        "fields": [
            {"key": "puppet", "label": "Puppet", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "chef",
        "group": "Agents and distro",
        "label": "Chef",
        "help": "Chef module.",
        "fields": [
            {"key": "chef", "label": "Chef", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "salt_minion",
        "group": "Agents and distro",
        "label": "Salt",
        "help": "Salt minion module.",
        "fields": [
            {"key": "salt_minion", "label": "Salt minion", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "mcollective",
        "group": "Agents and distro",
        "label": "MCollective",
        "help": "MCollective module.",
        "fields": [
            {"key": "mcollective", "label": "MCollective", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "landscape",
        "group": "Agents and distro",
        "label": "Landscape",
        "help": "Landscape module.",
        "fields": [
            {"key": "landscape", "label": "Landscape", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "ubuntu_pro",
        "group": "Agents and distro",
        "label": "Ubuntu Pro",
        "help": "Ubuntu Advantage / Pro module.",
        "fields": [
            {
                "key": "ubuntu_advantage",
                "label": "Ubuntu Pro",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "byobu",
        "group": "Agents and distro",
        "label": "Byobu",
        "help": "Byobu terminal multiplexer module.",
        "fields": [
            {"key": "byobu", "label": "Byobu", "type": "yaml", "yaml_mode": "value", "yaml_type": "any"},
        ],
    },
    {
        "id": "fan",
        "group": "Agents and distro",
        "label": "Fan",
        "help": "Fan networking module.",
        "fields": [
            {"key": "fan", "label": "Fan", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "lxd",
        "group": "Agents and distro",
        "label": "LXD",
        "help": "LXD module.",
        "fields": [
            {"key": "lxd", "label": "LXD", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "drivers",
        "group": "Agents and distro",
        "label": "Ubuntu drivers",
        "help": "Ubuntu drivers module.",
        "fields": [
            {"key": "drivers", "label": "Drivers", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "wireguard",
        "group": "Agents and distro",
        "label": "Wireguard",
        "help": "Wireguard module.",
        "fields": [
            {"key": "wireguard", "label": "Wireguard", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "grub_dpkg",
        "group": "Agents and distro",
        "label": "GRUB dpkg",
        "help": "GRUB dpkg module.",
        "fields": [
            {"key": "grub_dpkg", "label": "GRUB dpkg", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "rh_subscription",
        "group": "Agents and distro",
        "label": "Red Hat subscription",
        "help": "Red Hat subscription module.",
        "fields": [
            {
                "key": "rh_subscription",
                "label": "Red Hat subscription",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "dict",
            },
        ],
    },
    {
        "id": "spacewalk",
        "group": "Agents and distro",
        "label": "Spacewalk",
        "help": "Spacewalk module.",
        "fields": [
            {"key": "spacewalk", "label": "Spacewalk", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "rpi",
        "group": "Agents and distro",
        "label": "Raspberry Pi",
        "help": "Raspberry Pi module.",
        "fields": [
            {"key": "rpi", "label": "Raspberry Pi", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "random_seed",
        "group": "Agents and distro",
        "label": "Seed random",
        "help": "Random seed module.",
        "fields": [
            {"key": "random_seed", "label": "Random seed", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
    {
        "id": "disable_ec2_metadata",
        "group": "Agents and distro",
        "label": "Disable EC2 metadata",
        "help": "Disable EC2 metadata service lookups.",
        "fields": [
            {"key": "disable_ec2_metadata", "label": "Disable EC2 metadata", "type": "bool"},
        ],
    },
    {
        "id": "install_hotplug",
        "group": "Agents and distro",
        "label": "Install hotplug",
        "help": "Install hotplug module (any YAML value).",
        "fields": [
            {
                "key": "install_hotplug",
                "label": "Install hotplug",
                "type": "yaml",
                "yaml_mode": "value",
                "yaml_type": "any",
            },
        ],
    },
    {
        "id": "keys_to_console",
        "group": "Agents and distro",
        "label": "Keys to console",
        "help": "SSH fingerprint console blacklist.",
        "fields": [
            {
                "key": "ssh_fp_console_blacklist",
                "label": "SSH fingerprint blacklist",
                "type": "string_list",
            },
        ],
    },
    {
        "id": "reporting",
        "group": "Agents and distro",
        "label": "Reporting",
        "help": "Reporting module.",
        "fields": [
            {"key": "reporting", "label": "Reporting", "type": "yaml", "yaml_mode": "value", "yaml_type": "dict"},
        ],
    },
]


def _compose_nodes(hand: list[dict]) -> list[dict]:
    from .generated_nodes import GENERATED_NODES, PATCHES

    drop = {node["id"] for node in GENERATED_NODES} | {"reporting", "fs_setup"}
    nodes = [node for node in hand if node["id"] not in drop]
    for node in nodes:
        extra = PATCHES.get(node["id"]) or []
        if not extra:
            continue
        if node["id"] == "users":
            node["fields"][0]["item_fields"].extend(extra)
            node["fields"][0]["preserve_extra"] = True
            node["fields"][0]["allow_scalars"] = True
        elif node["id"] == "write_files":
            node["fields"][0]["item_fields"].extend(extra)
            node["fields"][0]["preserve_extra"] = True
        elif node["id"] == "ntp":
            node["fields"][0]["object_fields"].extend(extra)
            node["fields"][0]["preserve_extra"] = True
        elif node["id"] == "ssh":
            node["fields"] = [field for field in node["fields"] if field.get("type") != "yaml"]
            node["fields"].extend(extra)
        elif node["id"] == "keys_to_console":
            node["fields"].extend(extra)
    for node in GENERATED_NODES:
        if node["id"] == "yum_repos":
            node["fields"] = PATCHES.get("yum_repos", []) + node["fields"]
        if node["id"] == "passwords":
            continue
    nodes.extend(GENERATED_NODES)
    chpasswd = next(
        field for node in nodes if node["id"] == "passwords" for field in node["fields"] if field["key"] == "chpasswd"
    )
    users = next(field for field in chpasswd["object_fields"] if field["key"] == "users")
    users["entry_rule"] = "chpasswd"
    users["preserve_extra"] = True
    for item in users["item_fields"]:
        if item["key"] == "password":
            item["credential"] = True
            item["placeholder"] = "{{password}}"
    password = next(
        field for node in nodes if node["id"] == "passwords" for field in node["fields"] if field["key"] == "password"
    )
    password["credential"] = True
    password["placeholder"] = "{{password}}"
    _apply_placeholders(nodes)
    return nodes


def _apply_placeholders(nodes: list[dict]) -> None:
    import json
    from pathlib import Path

    path = Path(__file__).with_name("module_examples.json")
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    placeholders = payload.get("placeholders") or {}

    def walk(fields: list[dict], prefix: str) -> None:
        for field in fields:
            key = field.get("key")
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if field.get("credential"):
                field.setdefault(
                    "placeholder", "{{password_hash}}" if key in {"passwd", "hashed_passwd"} else "{{password}}"
                )
            elif (
                "placeholder" not in field
                and dotted in placeholders
                and field.get("type") in {"string", "text", "flex", "yaml_value"}
            ):
                field["placeholder"] = placeholders[dotted]
            if field.get("type") == "object":
                walk(field.get("object_fields") or [], dotted)
            if field.get("type") == "row_list":
                example = placeholders.get(dotted)
                if example and "placeholder" not in field:
                    field["placeholder"] = example
                walk(field.get("item_fields") or [], dotted)

    for node in nodes:
        walk(node.get("fields") or [], "")


def _collect_modeled_keys() -> frozenset[str]:
    keys: set[str] = set()
    for node in NODES:
        for field in node.get("fields", []):
            ftype = field.get("type")
            if ftype == "yaml":
                yaml_mode = field.get("yaml_mode", "value")
                if yaml_mode == "mapping":
                    keys.update(field.get("yaml_keys", []))
                else:
                    key = field.get("key")
                    if key and not str(key).startswith("__yaml_"):
                        keys.add(key)
            elif ftype == "object":
                obj_key = field.get("key")
                if obj_key:
                    keys.add(obj_key)
            elif ftype == "row_list":
                key = field.get("key")
                if key:
                    keys.add(key)
            else:
                key = field.get("key")
                if key and not str(key).startswith("__yaml_"):
                    keys.add(key)
    from .generated_nodes import ALIASES

    keys.update(ALIASES)
    return frozenset(keys)


NODES = _compose_nodes(NODES)
MODELED_KEYS: frozenset[str] = _collect_modeled_keys()

_KEY_LABELS: dict[str, str] = {}
for _node in NODES:
    _label = _node["label"]
    for _field in _node.get("fields", []):
        _ftype = _field.get("type")
        if _ftype == "yaml":
            _yaml_mode = _field.get("yaml_mode", "value")
            if _yaml_mode == "mapping":
                for _yk in _field.get("yaml_keys", []):
                    _KEY_LABELS.setdefault(_yk, _label)
            else:
                _yk = _field.get("key")
                if _yk and not str(_yk).startswith("__yaml_"):
                    _KEY_LABELS.setdefault(_yk, _label)
        elif _ftype == "object":
            _KEY_LABELS.setdefault(_field["key"], _label)
        elif _ftype == "row_list":
            _KEY_LABELS.setdefault(_field["key"], _label)
        else:
            _yk = _field.get("key")
            if _yk and not str(_yk).startswith("__yaml_"):
                _KEY_LABELS.setdefault(_yk, _label)


def node_label_for_key(key: str) -> str:
    return _KEY_LABELS.get(key, key)
