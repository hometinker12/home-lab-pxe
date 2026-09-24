"""Build the cloud-init editor node schema and module examples from a cloud-init checkout.

Usage:
  python scripts/extract_cloudinit_examples.py --src PATH [--check]

PATH is a cloud-init 26.2 tree (doc/module-docs and
cloudinit/config/schemas/schema-cloud-config-v1.json).

Without --check the script rewrites src/cloudinit/generated_nodes.py (then runs
``ruff format`` on it when ruff is installed) and src/cloudinit/module_examples.json.
With --check it writes nothing: it compares the data it would generate with the
committed files (semantically, so formatting does not matter) and exits 1 when stale.

Everything the editor models is described here: LAYOUT (groups, nodes, top-level keys),
LABELS, OVERRIDES, and the ordering / secret / text tables. Do not hand-edit the
generated files; change this script and regenerate.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

DOCS_VERSION = "26.2"
DOCS_URL = "https://docs.cloud-init.io/en/latest/reference/modules.html#mod-"
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = ROOT / "src" / "cloudinit" / "module_examples.json"
NODES_PATH = ROOT / "src" / "cloudinit" / "generated_nodes.py"
HELP_MAX = 200

# Display order. Each node: (id, label, module, top-level keys, help).
LAYOUT: list[tuple[str, list[tuple[str, str, str, list[str], str]]]] = [
    (
        "Identity",
        [
            (
                "hostname",
                "Hostname",
                "cc_set_hostname",
                ["hostname", "fqdn", "prefer_fqdn_over_hostname", "create_hostname_file", "preserve_hostname"],
                "Guest hostname and FQDN. {{hostname}} uses the machine hostname field.",
            ),
            (
                "timezone",
                "Timezone",
                "cc_timezone",
                ["timezone"],
                "IANA timezone for the guest. {{timezone}} uses the machine timezone field.",
            ),
            ("locale", "Locale", "cc_locale", ["locale", "locale_configfile"], "System locale. false skips it."),
            ("keyboard", "Keyboard", "cc_keyboard", ["keyboard"], "Keyboard layout. Omitted when no layout is set."),
        ],
    ),
    (
        "Users and SSH",
        [
            (
                "users",
                "Users",
                "cc_users_groups",
                ["users"],
                "Users to create. 'default' is the distro default user. Rows without a name are dropped on save.",
            ),
            (
                "default_user",
                "Default user",
                "cc_users_groups",
                ["user"],
                "Overrides the distro default_user from /etc/cloud/cloud.cfg.",
            ),
            ("groups", "Groups", "cc_users_groups", ["groups"], "Groups to create, optionally with member users."),
            (
                "passwords",
                "Passwords",
                "cc_set_passwords",
                ["password", "chpasswd"],
                "Password and chpasswd settings. Use {{password}} or {{password_hash}} placeholders.",
            ),
            (
                "ssh",
                "SSH",
                "cc_ssh",
                [
                    "ssh_authorized_keys",
                    "ssh_pwauth",
                    "disable_root",
                    "disable_root_opts",
                    "allow_public_ssh_keys",
                    "ssh_deletekeys",
                    "ssh_genkeytypes",
                    "ssh_quiet_keygen",
                    "ssh_keys",
                    "ssh_publish_hostkeys",
                    "no_ssh_fingerprints",
                    "authkey_hash",
                ],
                "SSH server settings. Host keys (ssh_keys) are separate from authorized keys.",
            ),
            (
                "ssh_import",
                "SSH import",
                "cc_ssh_import_id",
                ["ssh_import_id"],
                "Import SSH keys from Launchpad or GitHub.",
            ),
        ],
    ),
    (
        "Packages",
        [
            (
                "packages",
                "Packages",
                "cc_package_update_upgrade_install",
                ["packages", "package_update", "package_upgrade", "package_reboot_if_required"],
                "Packages to install. {{packages}} uses the machine packages field.",
            ),
            (
                "apt",
                "Apt",
                "cc_apt_configure",
                ["apt", "apt_pipelining"],
                "Apt mirrors, sources, proxies, and pipelining.",
            ),
            ("snap", "Snap", "cc_snap", ["snap"], "Snap assertions and snap commands."),
        ],
    ),
    (
        "Files and commands",
        [
            ("write_files", "Write files", "cc_write_files", ["write_files"], "Files written during cloud-init."),
            ("runcmd", "Run commands", "cc_runcmd", ["runcmd"], "Commands run once, late in the first boot."),
            ("bootcmd", "Boot commands", "cc_bootcmd", ["bootcmd"], "Commands run very early on every boot."),
            (
                "final_message",
                "Final message",
                "cc_final_message",
                ["final_message"],
                "Printed when cloud-init finishes.",
            ),
            ("vendor_data", "Vendor data", "cc_scripts_vendor", ["vendor_data"], "Whether vendor scripts run."),
        ],
    ),
    (
        "Disks",
        [
            ("growpart", "Growpart", "cc_growpart", ["growpart"], "Grow partitions to fill available space."),
            ("resize_rootfs", "Resize rootfs", "cc_resizefs", ["resize_rootfs"], "Resize the root filesystem."),
            (
                "disk_setup",
                "Disk setup",
                "cc_disk_setup",
                ["device_aliases", "disk_setup", "fs_setup"],
                "Partition tables, device aliases, and filesystems.",
            ),
            (
                "mounts",
                "Mounts",
                "cc_mounts",
                ["mounts", "mount_default_fields", "swap"],
                "fstab entries and swap files.",
            ),
        ],
    ),
    (
        "Network",
        [
            ("hosts", "Hosts", "cc_update_etc_hosts", ["manage_etc_hosts"], "Manage /etc/hosts."),
            (
                "resolv_conf",
                "Resolv.conf",
                "cc_resolv_conf",
                ["manage_resolv_conf", "resolv_conf"],
                "DNS resolver configuration.",
            ),
            ("ntp", "NTP", "cc_ntp", ["ntp"], "NTP client configuration."),
            ("wireguard", "WireGuard", "cc_wireguard", ["wireguard"], "WireGuard interfaces."),
            ("fan", "Fan", "cc_fan", ["fan"], "Ubuntu Fan networking."),
            ("install_hotplug", "Install hotplug", "cc_install_hotplug", ["updates"], "When to apply network updates."),
        ],
    ),
    (
        "Power and phone home",
        [
            ("phone_home", "Phone home", "cc_phone_home", ["phone_home"], "Post instance data to a URL when done."),
            (
                "power_state",
                "Power state",
                "cc_power_state_change",
                ["power_state"],
                "Power off or reboot after cloud-init. Omitted when no mode is set.",
            ),
        ],
    ),
    (
        "System",
        [
            ("ca_certs", "CA certificates", "cc_ca_certs", ["ca_certs"], "Trusted CA certificates."),
            ("rsyslog", "Rsyslog", "cc_rsyslog", ["rsyslog"], "Rsyslog configuration and remotes."),
            ("random_seed", "Seed random", "cc_seed_random", ["random_seed"], "Kernel random seed data."),
            (
                "disable_ec2_metadata",
                "Disable EC2 metadata",
                "cc_disable_ec2_metadata",
                ["disable_ec2_metadata"],
                "Block the EC2 metadata service with a route.",
            ),
            ("byobu", "Byobu", "cc_byobu", ["byobu_by_default"], "Byobu on login."),
            ("grub_dpkg", "GRUB dpkg", "cc_grub_dpkg", ["grub_dpkg"], "GRUB install device debconf settings."),
            (
                "keys_to_console",
                "Keys to console",
                "cc_keys_to_console",
                ["ssh", "ssh_key_console_blacklist", "ssh_fp_console_blacklist"],
                "Which SSH host keys and fingerprints are written to the console.",
            ),
        ],
    ),
    (
        "Ubuntu",
        [
            ("ubuntu_pro", "Ubuntu Pro", "cc_ubuntu_pro", ["ubuntu_pro"], "Ubuntu Pro attach token and services."),
            ("drivers", "Ubuntu drivers", "cc_ubuntu_drivers", ["drivers"], "Ubuntu driver packages."),
            ("lxd", "LXD", "cc_lxd", ["lxd"], "LXD init. Preseed is opaque YAML passed to lxd init."),
            ("landscape", "Landscape", "cc_landscape", ["landscape"], "Landscape client registration."),
        ],
    ),
    (
        "Config management",
        [
            ("ansible", "Ansible", "cc_ansible", ["ansible"], "Ansible install, pull, and controller setup."),
            ("puppet", "Puppet", "cc_puppet", ["puppet"], "Puppet agent install and configuration."),
            ("chef", "Chef", "cc_chef", ["chef"], "Chef client install and configuration."),
            ("salt_minion", "Salt", "cc_salt_minion", ["salt_minion"], "Salt minion configuration."),
            ("mcollective", "MCollective", "cc_mcollective", ["mcollective"], "MCollective server configuration."),
        ],
    ),
    (
        "Other distros",
        [
            ("yum_repos", "Yum repos", "cc_yum_add_repo", ["yum_repos", "yum_repo_dir"], "Yum repository files."),
            ("zypper", "Zypper", "cc_zypper_add_repo", ["zypper"], "Zypper repositories and zypp.conf."),
            ("apk_repos", "APK", "cc_apk_configure", ["apk_repos"], "Alpine APK repositories."),
            (
                "rh_subscription",
                "Red Hat subscription",
                "cc_rh_subscription",
                ["rh_subscription"],
                "Red Hat subscription-manager registration.",
            ),
            ("spacewalk", "Spacewalk", "cc_spacewalk", ["spacewalk"], "Spacewalk registration."),
            ("rpi", "Raspberry Pi", "cc_raspberry_pi", ["rpi"], "Raspberry Pi interfaces."),
        ],
    ),
]

# Top-level keys that stay in Advanced YAML (not modeled by any node).
ADVANCED_KEYS = [
    "autoinstall",
    "cloud_init_modules",
    "cloud_config_modules",
    "cloud_final_modules",
    "merge_how",
    "merge_type",
    "launch-index",
    "migrate",
    "output",
    "reporting",
    "system_info",
    "version",
]

# Preferred doc example per node (module, 1-based example number); default is the
# first example of the node's module that sets one of the node's keys.
NODE_EXAMPLE = {
    "users": ("cc_users_groups", 3),
    "lxd": ("cc_lxd", 2),
    "ntp": ("cc_ntp", 2),
    "packages": ("cc_package_update_upgrade_install", 1),
    "write_files": ("cc_write_files", 5),
}

WORDS = {
    "aio": "AIO",
    "api": "API",
    "ca": "CA",
    "csr": "CSR",
    "dhcp": "DHCP",
    "dir": "dir",
    "dns": "DNS",
    "ec2": "EC2",
    "ecdsa": "ECDSA",
    "ed25519": "Ed25519",
    "efi": "EFI",
    "exe": "executable",
    "fqdn": "FQDN",
    "ftp": "FTP",
    "gecos": "GECOS",
    "gpg": "GPG",
    "http": "HTTP",
    "https": "HTTPS",
    "i2c": "I2C",
    "id": "ID",
    "ipv4": "IPv4",
    "ipv6": "IPv6",
    "json": "JSON",
    "lxd": "LXD",
    "mtu": "MTU",
    "nat": "NAT",
    "ntp": "NTP",
    "pc": "PC",
    "pid": "PID",
    "pki": "PKI",
    "rhsm": "RHSM",
    "rsa": "RSA",
    "scp": "SCP",
    "sftp": "SFTP",
    "spi": "SPI",
    "ssh": "Console output",
    "ssl": "SSL",
    "ua": "UA",
    "uid": "UID",
    "uri": "URI",
    "url": "URL",
    "usb": "USB",
}

LABELS = {
    "hostname": "Short hostname",
    "fqdn": "FQDN",
    "prefer_fqdn_over_hostname": "Prefer FQDN",
    "create_hostname_file": "Create /etc/hostname",
    "preserve_hostname": "Preserve existing hostname",
    "locale_configfile": "Locale config file",
    "users": "Users",
    "users[].name": "Name",
    "users[].sudo": "Sudo rules",
    "users[].groups": "Groups",
    "users[].lock_passwd": "Lock password",
    "users[].hashed_passwd": "Hashed password",
    "users[].passwd": "Password hash (new users only)",
    "users[].plain_text_passwd": "Plain-text password",
    "users[].homedir": "Home directory",
    "users[].system": "System user",
    "users[].doas": "Doas rules",
    "users[].expiredate": "Expire date",
    "users[].inactive": "Inactive days",
    "users[].snapuser": "Snap user (email)",
    "user": "Default user",
    "groups": "Groups",
    "groups[].members": "Members",
    "password": "Password module",
    "chpasswd": "Chpasswd",
    "chpasswd.expire": "Expire passwords",
    "ssh_authorized_keys": "Authorized keys",
    "ssh_pwauth": "SSH password authentication",
    "disable_root": "Disable root login",
    "disable_root_opts": "Disable root options",
    "allow_public_ssh_keys": "Allow public SSH keys",
    "ssh_deletekeys": "Delete existing host keys",
    "ssh_genkeytypes": "Generate host key types",
    "ssh_quiet_keygen": "Quiet keygen",
    "ssh_keys": "Host keys",
    "ssh_publish_hostkeys": "Publish host keys",
    "no_ssh_fingerprints": "No SSH fingerprints",
    "authkey_hash": "Authorized key hash",
    "ssh_import_id": "SSH import IDs",
    "packages": "Packages",
    "package_update": "Update package index",
    "package_upgrade": "Upgrade packages",
    "package_reboot_if_required": "Reboot if required",
    "apt_pipelining": "Apt pipelining",
    "write_files": "Files",
    "write_files[].encoding": "Encoding",
    "runcmd": "Commands",
    "bootcmd": "Commands",
    "final_message": "Message",
    "mounts": "Mounts",
    "mounts[].fs_spec": "Device",
    "mounts[].fs_file": "Mount point",
    "mounts[].fs_vfstype": "Type",
    "mounts[].fs_mntops": "Options",
    "mounts[].fs_freq": "Dump",
    "mounts[].fs_passno": "Pass",
    "disk_setup": "Disks",
    "disk_setup[].name": "Disk or alias",
    "fs_setup": "Filesystems",
    "manage_etc_hosts": "Manage /etc/hosts",
    "manage_resolv_conf": "Manage resolv.conf",
    "resolv_conf": "Resolv.conf",
    "updates": "Updates",
    "phone_home.post": "POST fields",
    "keyboard.layout": "Layout",
    "ssh": "Console output",
    "ssh.emit_keys_to_console": "Emit host keys to console",
    "ssh_key_console_blacklist": "Key types not written to console",
    "ssh_fp_console_blacklist": "Fingerprint types not written to console",
    "byobu_by_default": "Byobu by default",
    "disable_ec2_metadata": "Disable EC2 metadata",
    "ca_certs.trusted": "Trusted certificates",
    "yum_repos": "Repositories",
    "yum_repos[]._key": "Repository ID",
    "yum_repo_dir": "Repository directory",
    "apt.sources[].name": "Source name",
    "apt.debconf_selections": "Debconf selections",
    "zypper.repos": "Repositories",
    "rpi.interfaces.serial": "Serial (true/false or console/hardware)",
    "power_state.condition": "Condition",
}

# Common fields first; anything not listed follows in schema order.
USER_ORDER = [
    "name",
    "gecos",
    "groups",
    "sudo",
    "shell",
    "lock_passwd",
    "ssh_authorized_keys",
    "hashed_passwd",
    "passwd",
    "plain_text_passwd",
    "primary_group",
    "homedir",
    "uid",
    "system",
    "ssh_import_id",
    "ssh_redirect_user",
    "doas",
    "expiredate",
    "inactive",
    "no_create_home",
    "no_log_init",
    "no_user_group",
    "create_groups",
    "selinux_user",
    "snapuser",
]

# Sub-field order per container path ("[]" = row items); unlisted keys follow in schema order.
ORDER = {
    "users[]": USER_ORDER,
    "user": USER_ORDER,
    "ansible.pull[]": ["url", "playbook_names", "playbook_name", "checkout"],
    "write_files[]": ["path", "content", "owner", "permissions", "encoding", "append", "defer", "source"],
}

# Required sub-fields the schema expresses only through oneOf/anyOf.
REQUIRED_PATHS = {"users[].name", "ansible.pull[].url"}

# Fields rendered as secrets (masked input / textarea).
SECRET_PATHS = {
    "ubuntu_pro.token",
    "landscape.client.registration_key",
    "lxd.init.trust_password",
    "rh_subscription.activation_key",
    "spacewalk.activation_key",
    "chef.validation_cert",
    "salt_minion.private_key",
    "mcollective.conf.private-cert",
    "ssh_keys.rsa_private",
    "ssh_keys.ecdsa_private",
    "ssh_keys.ed25519_private",
}

# String fields that hold multi-line text.
TEXT_PATHS = {
    "final_message",
    "fan.config",
    "apt.sources_list",
    "apt.conf",
    "apt.primary[].key",
    "apt.security[].key",
    "apt.sources[].key",
    "ntp.config.template",
    "lxd.preseed",
    "puppet.conf.ca_cert",
    "chef.validation_cert",
    "salt_minion.private_key",
    "salt_minion.public_key",
    "mcollective.conf.public-cert",
    "mcollective.conf.private-cert",
    "write_files[].content",
    "wireguard.interfaces[].content",
    "rsyslog.configs[].content",
    "random_seed.data",
    "ssh_keys.rsa_private",
    "ssh_keys.rsa_public",
    "ssh_keys.rsa_certificate",
    "ssh_keys.ecdsa_private",
    "ssh_keys.ecdsa_public",
    "ssh_keys.ecdsa_certificate",
    "ssh_keys.ed25519_private",
    "ssh_keys.ed25519_public",
    "ssh_keys.ed25519_certificate",
}

# Credential placeholders. Strict placeholder-only validation follows the editor rules.
CREDENTIAL_PATHS = {
    "password": "{{password}}",
    "chpasswd.users[].password": "{{password}}",
    "rh_subscription.password": "{{password}}",
    "users[].passwd": "{{password_hash}}",
    "users[].plain_text_passwd": "{{password}}",
    "user.passwd": "{{password_hash}}",
    "user.plain_text_passwd": "{{password}}",
}

ROW_META = {
    "users": ("user", "name"),
    "chpasswd.users": ("user", "name"),
    "groups": ("group", "name"),
    "write_files": ("file", "path"),
    "apt.primary": ("mirror", "uri"),
    "apt.security": ("mirror", "uri"),
    "apt.sources": ("source", "name"),
    "snap.assertions": ("assertion", "name"),
    "disk_setup": ("disk", "name"),
    "fs_setup": ("filesystem", "device"),
    "mounts": ("mount", "fs_spec"),
    "wireguard.interfaces": ("interface", "name"),
    "ca_certs.trusted": ("certificate", "value"),
    "rsyslog.configs": ("config", "filename"),
    "ansible.setup_controller.repositories": ("repository", "path"),
    "ansible.setup_controller.run_ansible": ("playbook", "playbook_name"),
    "ansible.pull": ("pull", "url"),
    "yum_repos": ("repository", "_key"),
    "zypper.repos": ("repository", "id"),
}


def _map(key_label: str = "Key", value_label: str = "Value", value_type: str = "scalar") -> dict:
    return {"type": "map", "value_type": value_type, "key_label": key_label, "value_label": value_label}


# Path-specific field shapes. Keys are dotted paths; "[]" marks row items.
OVERRIDES: dict[str, dict] = {
    "locale": {"type": "flex", "choices": ["false"]},
    "ssh_pwauth": {"type": "bool"},
    "apt_pipelining": {"type": "flex", "choices": ["os", "false"]},
    "users[].sudo": {"type": "string_or_list", "allow_false": True},
    "users[].groups": {"type": "string_or_list"},
    "users[].uid": {"type": "int"},
    "user.sudo": {"type": "string_or_list", "allow_false": True},
    "user.groups": {"type": "string_or_list"},
    "user.uid": {"type": "int"},
    "fs_setup[].cmd": {"type": "string_or_list"},
    "fs_setup[].extra_opts": {"type": "string_or_list"},
    "fs_setup[].partition": {"type": "flex", "choices": ["auto", "any", "none"]},
    "vendor_data.prefix": {"type": "string_or_list"},
    "vendor_data.enabled": {"type": "flex", "choices": ["true", "false"]},
    "power_state.delay": {"type": "flex", "choices": ["now"]},
    "power_state.condition": {"type": "yaml_value", "yaml_type": "any"},
    "swap.size": {"type": "flex", "choices": ["auto"]},
    "swap.maxsize": {"type": "flex"},
    "rh_subscription.org": {"type": "flex"},
    "grub_dpkg.grub-pc/install_devices_empty": {"type": "flex", "choices": ["true", "false"]},
    "rpi.interfaces.serial": {"type": "yaml_value", "yaml_type": "any"},
    "rsyslog.service_reload_command": {"type": "string_or_list"},
    "rsyslog.remotes": _map("Name", "Remote", "string"),
    "resolv_conf.options": _map("Option", "Value"),
    "resolv_conf.nameservers": {"type": "string_list"},
    "resolv_conf.searchdomains": {"type": "string_list"},
    "resolv_conf.sortlist": {"type": "string_list"},
    "zypper.config": _map("Option", "Value"),
    "write_files[].source.headers": _map("Header", "Value", "string"),
    "write_files[].permissions": {"type": "string", "octal": True, "placeholder": "0644", "default": None},
    "puppet.conf.main": _map("Setting", "Value"),
    "puppet.conf.server": _map("Setting", "Value"),
    "puppet.conf.agent": _map("Setting", "Value"),
    "puppet.conf.user": _map("Setting", "Value"),
    "puppet.csr_attributes.custom_attributes": _map("OID", "Value"),
    "puppet.csr_attributes.extension_requests": _map("Extension", "Value"),
    "chef.initial_attributes": {"type": "yaml_value", "yaml_type": "dict"},
    "salt_minion.conf": {"type": "yaml_value", "yaml_type": "dict"},
    "salt_minion.grains": {"type": "yaml_value", "yaml_type": "dict"},
    "apt.debconf_selections": _map("Name", "Selections", "string"),
    "device_aliases": _map("Alias", "Device", "string"),
    "phone_home": {"require_subkey": "url"},
    "power_state": {"require_subkey": "mode"},
    "keyboard": {"require_subkey": "layout"},
    "disk_setup[].layout": {"type": "disk_layout"},
    "runcmd": {"type": "command_list"},
    "bootcmd": {"type": "command_list"},
    "snap.commands": {"type": "command_list"},
    "ansible.galaxy.actions": {"type": "command_list"},
    "packages": {"type": "package_list", "block_token": "packages", "managers": ["apt", "snap"]},
    "ssh_authorized_keys": {"type": "string_list", "block_token": "ssh_keys"},
    "users[].ssh_authorized_keys": {"type": "string_list", "block_token": "ssh_keys"},
    "user.ssh_authorized_keys": {"type": "string_list", "block_token": "ssh_keys"},
    "phone_home.post": {
        "type": "all_or_list",
        "choices": ["pub_key_rsa", "pub_key_ecdsa", "pub_key_ed25519", "instance_id", "hostname", "fqdn"],
    },
    "groups": {
        "type": "row_list",
        "emit": "groups",
        "key_field": "name",
        "item_fields": [
            {"key": "name", "label": "Group", "type": "string", "required": True},
            {"key": "members", "label": "Members", "type": "string_list"},
        ],
    },
    "snap.assertions": {
        "type": "row_list",
        "emit": "list_or_map",
        "key_field": "name",
        "item_fields": [
            {"key": "name", "label": "Name (map form only)", "type": "string"},
            {"key": "value", "label": "Assertion", "type": "text"},
        ],
    },
    "ca_certs.trusted": {
        "type": "row_list",
        "emit": "list_or_map",
        "item_fields": [{"key": "value", "label": "Certificate", "type": "text"}],
    },
    "rsyslog.configs": {
        "type": "row_list",
        "allow_scalars": True,
        "preserve_extra": True,
        "item_fields": [
            {"key": "filename", "label": "Filename", "type": "string"},
            {"key": "content", "label": "Content", "type": "text", "required": True},
        ],
    },
    "ansible.setup_controller.repositories": {
        "type": "row_list",
        "preserve_extra": True,
        "item_fields": [
            {"key": "path", "label": "Path", "type": "string", "required": True},
            {"key": "source", "label": "Source", "type": "string", "required": True},
        ],
    },
    "mounts": {
        "type": "row_list",
        "emit": "tuple",
        "item_fields": [
            {"key": col, "label": col, "type": "string"}
            for col in ("fs_spec", "fs_file", "fs_vfstype", "fs_mntops", "fs_freq", "fs_passno")
        ],
    },
    "mount_default_fields": {"type": "string_list"},
    "updates.network.when": {"type": "enum_list"},
    "ssh_genkeytypes": {"type": "enum_list"},
    "chpasswd.users": {"entry_rule": "chpasswd"},
    "chpasswd.users[].type": {"type": "enum", "choices": ["", "hash", "text", "RANDOM"]},
}

# Keys owned elsewhere or kept only via "Other keys" YAML.
SKIP_PATHS = {"chpasswd.list"}


class Ctx:
    def __init__(self, root: dict) -> None:
        self.root = root
        self.defs = root["$defs"]


def _resolve(node: object, ctx: Ctx) -> dict:
    """Resolve $ref and allOf into one mapping (sibling keys win)."""
    if not isinstance(node, dict):
        return {}
    node = dict(node)
    if "$ref" in node:
        ref = str(node.pop("$ref"))
        base = _resolve(ctx.defs[ref.split("/")[-1]], ctx) if ref.startswith("#/$defs/") else {}
        merged = dict(base)
        for key, value in node.items():
            if key == "properties" and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        node = merged
    if "allOf" in node:
        parts = node.pop("allOf")
        merged: dict = {}
        for part in parts:
            piece = _resolve(part, ctx)
            for key, value in piece.items():
                if key == "properties" and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        for key, value in node.items():
            if key == "properties" and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        node = merged
    return node


def _types(schema: dict) -> set[str]:
    raw = schema.get("type")
    if isinstance(raw, list):
        return set(raw)
    if isinstance(raw, str):
        return {raw}
    return set()


def _branches(schema: dict, ctx: Ctx) -> list[dict]:
    options = schema.get("oneOf") or schema.get("anyOf") or []
    return [_resolve(option, ctx) for option in options]


def _all_types(schema: dict, ctx: Ctx) -> set[str]:
    kinds = set(_types(schema))
    for branch in _branches(schema, ctx):
        kinds |= _all_types(branch, ctx)
    if not kinds and "enum" in schema:
        for value in schema["enum"]:
            kinds.add("boolean" if isinstance(value, bool) else "string")
    if not kinds and ("properties" in schema or "patternProperties" in schema):
        kinds.add("object")
    return kinds


def _strip_rst(text: str) -> str:
    text = re.sub(r":[a-z]+:`([^`<]*?)\s*<[^>]*>`", r"\1", text)
    text = re.sub(r":[a-z]+:`([^`]*)`", r"\1", text)
    text = re.sub(r"`([^`<]*?)\s*<[^>]*>`_+", r"\1", text)
    text = re.sub(r"``([^`]*)``", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?!\w)", r"\1", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str) -> str:
    text = _strip_rst(text)
    if len(text) <= HELP_MAX:
        return text
    cut = text[:HELP_MAX]
    stop = max(cut.rfind(". "), cut.rfind(".\n"))
    if stop >= 60:
        return cut[: stop + 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip(",;:") + "…"


def _help(schema: dict) -> str:
    desc = schema.get("description")
    return _short(desc) if isinstance(desc, str) and desc.strip() else ""


def _deprecated(schema: dict) -> str:
    if not schema.get("deprecated"):
        return ""
    version = schema.get("deprecated_version")
    note = _strip_rst(str(schema.get("deprecated_description") or "")).strip()
    head = f"Deprecated in {version}." if version else "Deprecated."
    return _short(f"{head} {note}".strip())


def _words(key: str) -> str:
    parts = re.split(r"[_\-\s]+", key.replace("/", " / "))
    out = []
    for index, part in enumerate(parts):
        if not part:
            continue
        low = part.lower()
        if low in WORDS:
            out.append(WORDS[low])
        elif index == 0:
            out.append(part[:1].upper() + part[1:])
        else:
            out.append(part)
    text = " ".join(out).replace(" / ", "/")
    return text[:1].upper() + text[1:] if text else key


def _label(path: str, key: str) -> str:
    if path in LABELS:
        return LABELS[path]
    if path.startswith("user."):
        return LABELS.get("users[]." + path[len("user.") :]) or _words(key)
    return _words(key)


def _default(schema: dict) -> object:
    value = schema.get("default")
    if isinstance(value, (dict, list)):
        return None
    if isinstance(value, str) and value.startswith("``") and value.endswith("``"):
        return None
    return value


def _canonical(props: dict, ctx: Ctx) -> tuple[dict, dict[str, str]]:
    """Drop deprecated hyphenated duplicates; return kept props and alias -> canonical."""
    aliases: dict[str, str] = {}
    kept: dict = {}
    for key, value in props.items():
        if "-" in key and "/" not in key:
            underscored = key.replace("-", "_")
            if underscored in props and underscored != key:
                aliases[key] = underscored
                continue
        kept[key] = value
    return kept, aliases


def _object_props(schema: dict, ctx: Ctx) -> tuple[dict, set[str]]:
    """Properties of an object schema merged across its oneOf/anyOf branches."""
    props: dict = dict(schema.get("properties") or {})
    for branch in _branches(schema, ctx):
        for key, value in (branch.get("properties") or {}).items():
            props.setdefault(key, value)
    return props, set(schema.get("required") or [])


def _sub_fields(prefix: str, schema: dict, ctx: Ctx) -> tuple[list[dict], dict[str, str]]:
    props, required = _object_props(schema, ctx)
    props, aliases = _canonical(props, ctx)
    fields: dict[str, dict] = {}
    for prop, prop_schema in props.items():
        path = f"{prefix}.{prop}"
        sub = convert(path, prop, prop_schema, ctx)
        if sub is None:
            continue
        if prop in required or path in REQUIRED_PATHS:
            sub["required"] = True
        fields[prop] = sub
    ordered = [fields.pop(key) for key in ORDER.get(prefix, []) if key in fields]
    ordered.extend(fields.values())
    return ordered, aliases


def _base(path: str, key: str, schema: dict) -> dict:
    field: dict = {"key": key, "label": _label(path, key)}
    return field


def _finish(field: dict, path: str, schema: dict) -> dict:
    help_text = _help(schema)
    if help_text:
        field["help"] = help_text
    deprecated = _deprecated(schema)
    if deprecated:
        field["deprecated"] = deprecated
    if "default" not in field and field["type"] in {"string", "bool", "int", "enum", "flex", "text"}:
        field["default"] = _default(schema)
    if field.get("default") is None or field.get("default") == "":
        field.pop("default", None)
    if path in SECRET_PATHS:
        field["secret"] = True
    if path in CREDENTIAL_PATHS:
        field["credential"] = True
        field["placeholder"] = CREDENTIAL_PATHS[path]
    if field["type"] == "row_list":
        meta = ROW_META.get(path)
        item_fields = field.get("item_fields") or []
        if meta:
            field["item_label"], field["summary_key"] = meta
        else:
            field["item_label"] = "item"
            summary = field.get("key_field") if field.get("emit") in {"mapping", "mapping_scalar"} else None
            if not summary:
                summary = next((f["key"] for f in item_fields if f.get("required")), None)
            if not summary:
                summary = next((f["key"] for f in item_fields if f["type"] == "string"), None)
            if summary:
                field["summary_key"] = summary
    return field


def _apply_override(field: dict, override: dict) -> dict:
    if "type" in override and override["type"] != field.get("type"):
        keep = {"key", "label", "required"}
        field = {k: v for k, v in field.items() if k in keep}
    field.update(copy.deepcopy(override))
    return field


def _enum_choices(schema: dict, ctx: Ctx) -> list[str] | None:
    """Return choices when every branch is an enum/const of scalars (deprecated branches kept)."""
    options = _branches(schema, ctx) or [schema]
    choices: list[str] = []
    for option in options:
        values = option.get("enum")
        if values is None and "const" in option:
            values = [option["const"]]
        if values is None:
            return None
        for value in values:
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            if text not in choices:
                choices.append(text)
    return choices


def _flex_choices(schema: dict, ctx: Ctx) -> list[str]:
    out: list[str] = []
    for option in _branches(schema, ctx):
        if option.get("deprecated"):
            continue
        for value in option.get("enum") or []:
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            if text not in out:
                out.append(text)
    return out


def convert(path: str, key: str, schema: object, ctx: Ctx) -> dict | None:
    schema = _resolve(schema, ctx)
    if path in SKIP_PATHS:
        return None
    override = OVERRIDES.get(path)
    if key == "ssh_keys" and path == "ssh_keys":
        field = _ssh_keys(ctx, schema)
    elif override and override.get("type") in {"row_list"} and "item_fields" in override:
        field = _apply_override(_base(path, key, schema), override)
        field["item_fields"] = [_finish(dict(item), f"{path}[].{item['key']}", {}) for item in field["item_fields"]]
        field["item_fields"] = [_label_item(item, f"{path}[]") for item in field["item_fields"]]
    else:
        field = _convert_generic(path, key, schema, ctx)
        if field is None:
            return None
        if override:
            field = _apply_override(field, override)
            if field["type"] == "enum_list" and "choices" not in field:
                items = _resolve(schema.get("items") or {}, ctx)
                field["choices"] = [str(value) for value in items.get("enum") or []]
            if field["type"] == "enum" and "choices" not in field:
                field["choices"] = [""] + (_enum_choices(schema, ctx) or [])
    return _finish(field, path, schema)


def _label_item(item: dict, prefix: str) -> dict:
    path = f"{prefix}.{item['key']}"
    if path in LABELS:
        item["label"] = LABELS[path]
    return item


def _ssh_keys(ctx: Ctx, schema: dict) -> dict:
    fields = []
    for kind in ("rsa", "ecdsa", "ed25519"):
        for part in ("private", "public", "certificate"):
            key = f"{kind}_{part}"
            path = f"ssh_keys.{key}"
            field = {"key": key, "label": _words(key), "type": "text" if path in TEXT_PATHS else "string"}
            fields.append(_finish(field, path, {}))
    return {
        "key": "ssh_keys",
        "label": _label("ssh_keys", "ssh_keys"),
        "type": "object",
        "object_fields": fields,
        "preserve_extra": True,
    }


def _scalar_field(path: str, key: str, schema: dict, ctx: Ctx, kinds: set[str]) -> dict:
    field = _base(path, key, schema)
    choices = _enum_choices(schema, ctx) if ("enum" in schema or _branches(schema, ctx)) else None
    if choices and kinds <= {"string", "boolean"}:
        field["type"] = "enum"
        field["choices"] = [""] + choices
        return field
    if kinds == {"boolean"}:
        field["type"] = "bool"
        return field
    if kinds and kinds <= {"integer", "number"}:
        field["type"] = "int"
        return field
    if kinds <= {"string", "null"} and "string" in kinds:
        field["type"] = "text" if path in TEXT_PATHS else "string"
        return field
    field["type"] = "flex"
    flex_choices = _flex_choices(schema, ctx)
    if flex_choices:
        field["choices"] = flex_choices
    return field


def _convert_generic(path: str, key: str, schema: dict, ctx: Ctx) -> dict | None:
    kinds = _all_types(schema, ctx)
    if kinds == {"null"}:
        return None
    if not kinds & {"object", "array"}:
        return _scalar_field(path, key, schema, ctx, kinds or {"string"})
    if "array" in kinds and "object" not in kinds and not (kinds - {"array", "null"}):
        return _array_field(path, key, schema, ctx)
    if kinds <= {"object", "null"}:
        return _object_field(path, key, schema, ctx)
    object_branch = next((b for b in _branches(schema, ctx) if "properties" in b), None)
    if object_branch is not None and "array" not in kinds and kinds - {"object"} <= {"string", "null"}:
        return _object_field(path, key, {**object_branch, "description": schema.get("description")}, ctx)
    if kinds == {"array", "string"}:
        return {**_base(path, key, schema), "type": "string_or_list"}
    if "object" in kinds and "array" in kinds:
        options = [b for b in _branches(schema, ctx) if "array" in _types(b)]
        if options:
            return _array_field(path, key, {**options[0], "description": schema.get("description")}, ctx)
        return _array_field(path, key, schema, ctx)
    return {**_base(path, key, schema), "type": "yaml_value", "yaml_type": "any"}


def _array_field(path: str, key: str, schema: dict, ctx: Ctx) -> dict:
    items = _resolve(schema.get("items") or {}, ctx)
    options = _branches(schema, ctx)
    if not items and options:
        arrays = [o for o in options if "array" in _types(o)]
        if arrays:
            items = _resolve(arrays[0].get("items") or {}, ctx)
    item_kinds = _all_types(items, ctx)
    field = _base(path, key, schema)
    if "object" in item_kinds or "properties" in items:
        obj = items
        if "properties" not in items and "object" not in _types(items):
            obj = next((b for b in _branches(items, ctx) if "properties" in b), items)
        item_fields, aliases = _sub_fields(f"{path}[]", obj, ctx)
        field.update({"type": "row_list", "item_fields": item_fields, "preserve_extra": True})
        if aliases:
            field["aliases"] = aliases
        if "string" in item_kinds:
            field["allow_scalars"] = True
        return field
    if "enum" in items and _types(items) <= {"string"}:
        field.update({"type": "enum_list", "choices": [str(v) for v in items["enum"]]})
        return field
    if item_kinds <= {"string", "null"} or not item_kinds:
        field["type"] = "string_list"
        return field
    field.update({"type": "yaml_value", "yaml_type": "list"})
    return field


def _object_field(path: str, key: str, schema: dict, ctx: Ctx) -> dict:
    pattern = schema.get("patternProperties") or {}
    properties = schema.get("properties") or {}
    field = _base(path, key, schema)
    if pattern and not properties:
        value_schema = _resolve(next(iter(pattern.values())), ctx)
        value_kinds = _all_types(value_schema, ctx)
        if value_kinds and not value_kinds & {"object", "array"}:
            field.update(_map(value_type="string" if value_kinds <= {"string"} else "scalar"))
            return field
        if "object" in value_kinds and "properties" in value_schema:
            sub_fields, aliases = _sub_fields(f"{path}[]", value_schema, ctx)
            key_name = "_key" if any(sub["key"] == "name" for sub in sub_fields) else "name"
            key_field = {"key": key_name, "label": _label(f"{path}[].{key_name}", "key"), "type": "string"}
            key_field["required"] = True
            item_fields = [key_field, *sub_fields]
            field.update(
                {
                    "type": "row_list",
                    "emit": "mapping",
                    "key_field": key_name,
                    "item_fields": item_fields,
                    "preserve_extra": True,
                }
            )
            if aliases:
                field["aliases"] = aliases
            return field
        field.update({"type": "yaml_value", "yaml_type": "dict"})
        return field
    if not properties:
        field.update({"type": "yaml_value", "yaml_type": "dict"})
        return field
    object_fields, aliases = _sub_fields(path, schema, ctx)
    field.update({"type": "object", "object_fields": object_fields, "preserve_extra": True})
    if aliases:
        field["aliases"] = aliases
    return field


def _module_schema(ctx: Ctx, module: str, key: str) -> dict:
    props = (ctx.defs.get(module) or {}).get("properties") or {}
    if key in props:
        return props[key]
    for name, definition in ctx.defs.items():
        if name.startswith("cc_") and key in (definition.get("properties") or {}):
            return definition["properties"][key]
    raise KeyError(f"{key} not found in schema")


def _top_field(ctx: Ctx, module: str, key: str) -> dict:
    field = convert(key, key, _module_schema(ctx, module, key), ctx)
    if field is None:
        raise KeyError(key)
    return field


def _top_aliases(ctx: Ctx) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for name, definition in ctx.defs.items():
        if not name.startswith("cc_"):
            continue
        for key, prop in (definition.get("properties") or {}).items():
            resolved = _resolve(prop, ctx)
            if not resolved.get("deprecated"):
                continue
            match = re.search(r"Use \*\*([A-Za-z0-9_\-]+)\*\*", str(resolved.get("deprecated_description") or ""))
            if match and match.group(1) in ctx.root.get("properties", {}) and match.group(1) != key:
                aliases[key] = match.group(1)
    return dict(sorted(aliases.items()))


def build_nodes(root_schema: dict) -> dict:
    ctx = Ctx(root_schema)
    nodes: list[dict] = []
    for group, entries in LAYOUT:
        for node_id, label, module, keys, help_text in entries:
            fields = [_top_field(ctx, module, key) for key in keys]
            if len(fields) == 1 and keys[0] not in LABELS:
                fields[0]["label"] = label
            nodes.append(
                {
                    "id": node_id,
                    "group": group,
                    "label": label,
                    "help": help_text,
                    "module": module,
                    "doc_url": DOCS_URL + module.replace("_", "-"),
                    "fields": fields,
                }
            )
    aliases = _top_aliases(ctx)
    modeled = {field["key"] for node in nodes for field in node["fields"]}
    schema_keys = sorted(root_schema.get("properties") or {})
    unowned = sorted(set(schema_keys) - modeled - set(aliases) - set(ADVANCED_KEYS))
    if unowned:
        raise SystemExit(f"schema keys without an owner node or ADVANCED_KEYS entry: {unowned}")
    return {
        "ALIASES": aliases,
        "ADVANCED_KEYS": list(ADVANCED_KEYS),
        "SCHEMA_KEYS": schema_keys,
        "GENERATED_NODES": nodes,
    }


def _dump_snippet(value: object) -> str:
    text = yaml.safe_dump(value, default_flow_style=False, sort_keys=False).rstrip()
    if text.endswith("\n..."):
        text = text[: -len("\n...")]
    return text.strip()


def _walk_examples(value: object, prefix: str, out: dict[str, str]) -> None:
    snippet = _dump_snippet(value)
    if prefix and snippet:
        out.setdefault(prefix, snippet)
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk_examples(child, path, out)
    elif isinstance(value, list) and value and isinstance(value[0], (dict, list)):
        _walk_examples(value[0], prefix, out)


def _example_body(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    kept = [line for line in lines if line.strip() not in {"#cloud-config", "## template: jinja"}]
    return "\n".join(kept).strip("\n") + "\n"


def build_examples(src: Path, nodes: list[dict]) -> dict:
    docs = src / "doc" / "module-docs"
    placeholders: dict[str, str] = {}
    examples: dict[str, list[str]] = {}
    for data_path in sorted(docs.glob("*/data.yaml")):
        meta = yaml.safe_load(data_path.read_text(encoding="utf-8")) or {}
        module_name = next(iter(meta), "")
        module = meta.get(module_name) if isinstance(meta, dict) else None
        entries = module.get("examples") if isinstance(module, dict) else None
        if not entries:
            continue
        texts: list[str] = []
        for index, example in enumerate(entries):
            file_name = example.get("file") if isinstance(example, dict) else None
            if not file_name or not (docs / file_name).is_file():
                continue
            raw = (docs / file_name).read_text(encoding="utf-8")
            texts.append(_example_body(raw))
            if index == 0:
                loaded = yaml.safe_load(raw)
                if isinstance(loaded, dict):
                    _walk_examples(loaded, "", placeholders)
        if texts:
            examples[module_name] = texts
    node_examples: dict[str, list] = {}
    for node in nodes:
        module = node["module"]
        texts = examples.get(module) or []
        keys = {field["key"] for field in node["fields"]}
        choice = None
        if node["id"] in NODE_EXAMPLE and NODE_EXAMPLE[node["id"]][0] == module:
            choice = NODE_EXAMPLE[node["id"]][1] - 1
        else:
            for index, text in enumerate(texts):
                loaded = yaml.safe_load(text)
                if isinstance(loaded, dict) and keys & set(loaded):
                    choice = index
                    break
        if choice is not None and choice < len(texts):
            node_examples[node["id"]] = [module, choice]
    return {
        "version": DOCS_VERSION,
        "placeholders": placeholders,
        "examples": examples,
        "node_examples": node_examples,
    }


def _py(value: object, indent: int = 0) -> str:
    pad = "    " * (indent + 1)
    end = "    " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        inner = ",\n".join(f"{pad}{json.dumps(k, ensure_ascii=False)}: {_py(v, indent + 1)}" for k, v in value.items())
        return "{\n" + inner + ",\n" + end + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        inner = ",\n".join(f"{pad}{_py(v, indent + 1)}" for v in value)
        return "[\n" + inner + ",\n" + end + "]"
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


_NODE_TYPES = {
    "ALIASES": "dict[str, str]",
    "ADVANCED_KEYS": "list[str]",
    "SCHEMA_KEYS": "list[str]",
    "GENERATED_NODES": "list[dict]",
}


def render_nodes_module(data: dict) -> str:
    parts = [
        f'"""Generated from cloud-init {DOCS_VERSION}. Regenerate with scripts/extract_cloudinit_examples.py."""',
        "",
        "from __future__ import annotations",
        "",
        f'DOCS_VERSION = "{DOCS_VERSION}"',
        "",
    ]
    for name, annotation in _NODE_TYPES.items():
        parts.append(f"{name}: {annotation} = {_py(data[name])}")
        parts.append("")
    return "\n".join(parts)


def read_nodes_module(path: Path) -> dict:
    if not path.is_file():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict = {}
    for stmt in tree.body:
        target = None
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target = stmt.target.id
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target = stmt.targets[0].id
        if target in _NODE_TYPES and stmt.value is not None:
            out[target] = ast.literal_eval(stmt.value)
    return out


def write_outputs(data: dict, examples: dict) -> None:
    NODES_PATH.write_text(render_nodes_module(data), encoding="utf-8")
    try:
        subprocess.run([sys.executable, "-m", "ruff", "format", str(NODES_PATH)], check=False, capture_output=True)
    except OSError:
        pass
    EXAMPLES_PATH.write_text(json.dumps(examples, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_outputs(data: dict, examples: dict) -> list[str]:
    stale: list[str] = []
    current_nodes = read_nodes_module(NODES_PATH)
    for name in _NODE_TYPES:
        if current_nodes.get(name) != data[name]:
            stale.append(f"{NODES_PATH.name}:{name}")
    try:
        current_examples = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current_examples = None
    if current_examples != examples:
        stale.append(EXAMPLES_PATH.name)
    return stale


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="compare only; never writes files")
    args = parser.parse_args()
    schema_path = args.src / "cloudinit" / "config" / "schemas" / "schema-cloud-config-v1.json"
    root_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    data = build_nodes(root_schema)
    examples = build_examples(args.src, data["GENERATED_NODES"])
    if args.check:
        stale = check_outputs(data, examples)
        if stale:
            print("stale generated data: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("generated nodes and module examples are up to date")
        return 0
    write_outputs(data, examples)
    count = sum(len(texts) for texts in examples["examples"].values())
    print(f"wrote {len(data['GENERATED_NODES'])} nodes, {count} examples, {len(examples['placeholders'])} placeholders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
