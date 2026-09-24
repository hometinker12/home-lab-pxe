"""Ubuntu autoinstall (Subiquity) editor schema: one node per installer section.

Hand-written from the autoinstall reference
(https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html).
Nodes and fields use the same shape and field types as the cloud-config nodes in ``schema.py``, so
``editor.py`` views and emits them with the same code. Node ids carry an ``ai_`` prefix so they never
collide with cloud-config node ids; field keys are the real installer keys.

Extra field attributes used here:

* ``locked`` (command_list): the form shows the list read-only until the operator confirms an unlock.
* ``managed_commands`` (command_list): commands home-lab-pxe depends on. ``match`` substrings mark a
  command as managed; ``readded`` says whether ``seed_render.complete_linux_user_data`` adds it back
  when the seed is served and the command is missing.
* ``lock_warning`` (command_list): the warning shown before unlocking.
"""

from __future__ import annotations

INSTALLER_GROUP = "Installer (autoinstall)"
_REF = "https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html"


def _doc(anchor: str) -> str:
    return f"{_REF}#{anchor}"


# Commands home-lab-pxe relies on. Keep in step with seed_render.complete_linux_user_data.
EARLY_MANAGED = [
    {
        "label": "Imaging callback",
        "match": ["{{imaging_url}}", "event=imaging"],
        "readded": True,
        "help": "Marks the machine as Imaging when the installer starts.",
    },
    {
        "label": "Wi-Fi quieting",
        "match": ["phy80211"],
        "readded": True,
        "help": "Takes Wi-Fi cards down so a probing card cannot abort the installer's network step.",
    },
    {
        "label": "Install confirm",
        "match": ["autoinstall-confirm.py"],
        "readded": True,
        "help": "Answers the installer's final yes/no prompt so the install stays unattended.",
    },
]
LATE_MANAGED = [
    {
        "label": "Phone-home",
        "match": ["{{phone_home_url}}"],
        "readded": False,
        "help": "Marks the machine as Deployed; without it the install ends in Timeout Error.",
    },
    {
        "label": "UEFI boot order",
        "match": ["uefi-boot-order.py"],
        "readded": True,
        "help": "Sets the UEFI boot order from the machine's Next Boot Device.",
    },
    {
        "label": "Forced reboot",
        "match": ["sysrq-trigger", "reboot -f"],
        "readded": True,
        "help": "Reboots straight away so NFS installs do not hang while unmounting.",
    },
]
ERROR_MANAGED = [
    {
        "label": "Failure log upload",
        "match": ["{{install_log_url}}", "install-log"],
        "readded": True,
        "help": "Uploads the installer log to home-lab-pxe when an install fails.",
    },
]

EARLY_WARNING = (
    "home-lab-pxe uses these commands. The imaging callback ({{imaging_url}}) marks the machine as Imaging, "
    "and Wi-Fi quieting stops a probing Wi-Fi card from aborting the install. When the seed is served, "
    "home-lab-pxe adds the imaging callback, Wi-Fi quieting and the install-confirm helper back if they are "
    "missing, but an edited copy is served as you wrote it. Changing these commands could break install "
    "tracking or other home-lab-pxe features."
)
LATE_WARNING = (
    "home-lab-pxe uses these commands. The phone-home callback ({{phone_home_url}}) marks the machine as "
    "Deployed, and the forced reboot stops NFS installs from hanging. When the seed is served, the forced "
    "reboot (and the UEFI boot-order helper, when a Next Boot Device is set) is added back if it is missing. "
    "The phone-home callback is not added back: if you remove or break it, the machine never reaches "
    "Deployed and ends in Timeout Error."
)
ERROR_WARNING = (
    "home-lab-pxe uses this command to upload the installer log ({{install_log_url}}) when an install "
    "fails, so the failure shows on the machine page. When the seed is served, the log upload is added back "
    "if it is missing, but an edited copy is served as you wrote it. Changing these commands could break "
    "failure reporting."
)


def _commands(key: str, label: str, help_text: str, managed: list[dict], warning: str) -> dict:
    return {
        "key": key,
        "label": label,
        "type": "command_list",
        "help": help_text,
        "locked": True,
        "managed_commands": managed,
        "lock_warning": warning,
    }


def _node(node_id: str, label: str, help_text: str, anchor: str, fields: list[dict]) -> dict:
    return {
        "id": node_id,
        "group": INSTALLER_GROUP,
        "label": label,
        "help": help_text,
        "module": "autoinstall",
        "doc_url": _doc(anchor),
        "scope": "installer",
        "fields": fields,
    }


def _obj(key: str, label: str, fields: list[dict], **extra) -> dict:
    return {"key": key, "label": label, "type": "object", "object_fields": fields, "preserve_extra": True, **extra}


def _str(key: str, label: str, help_text: str = "", **extra) -> dict:
    field = {"key": key, "label": label, "type": "string", **extra}
    if help_text:
        field["help"] = help_text
    return field


def _bool(key: str, label: str, help_text: str = "", **extra) -> dict:
    field = {"key": key, "label": label, "type": "bool", **extra}
    if help_text:
        field["help"] = help_text
    return field


def _enum(key: str, label: str, choices: list[str], help_text: str = "", **extra) -> dict:
    field = {"key": key, "label": label, "type": "enum", "choices": ["", *choices], **extra}
    if help_text:
        field["help"] = help_text
    return field


def _yaml(key: str, label: str, yaml_type: str, help_text: str = "", **extra) -> dict:
    field = {"key": key, "label": label, "type": "yaml_value", "yaml_type": yaml_type, **extra}
    if help_text:
        field["help"] = help_text
    return field


_ETHERNET_FIELDS = [
    _str("id", "Interface ID", "Netplan name for this entry, for example zz-all-en.", required=True),
    _obj(
        "match",
        "Match",
        [
            _str("name", "Name", "Interface name; globs such as en* match many.", placeholder="en*"),
            _str("macaddress", "MAC address", placeholder="aa:bb:cc:dd:ee:ff"),
            _str("driver", "Driver", placeholder="e1000e"),
        ],
    ),
    _str("set-name", "Set name", "Rename the matched interface."),
    _bool("dhcp4", "DHCPv4"),
    _bool("dhcp6", "DHCPv6"),
    _bool("optional", "Optional", "Boot does not wait for this interface. Keep catch-all matches optional."),
    {
        "key": "addresses",
        "label": "Addresses",
        "type": "string_list",
        "help": "Static addresses in CIDR form.",
        "placeholder": "192.168.1.20/24",
    },
    _obj(
        "nameservers",
        "Nameservers",
        [
            {"key": "addresses", "label": "Addresses", "type": "string_list", "placeholder": "192.168.1.1"},
            {"key": "search", "label": "Search domains", "type": "string_list", "placeholder": "lan"},
        ],
    ),
    _yaml("routes", "Routes", "list", "Netplan routes, for example - to: default / via: 192.168.1.1."),
    _str("gateway4", "Gateway (IPv4)", deprecated="Deprecated in netplan; use routes."),
]

_MIRROR_FIELDS = [
    _str("uri", "URI", placeholder="http://archive.ubuntu.com/ubuntu"),
    _bool("country-mirror", "Country mirror", "Use the country mirror for the installer's locale."),
    {"key": "arches", "label": "Architectures", "type": "string_list", "placeholder": "amd64"},
]

INSTALLER_NODES: list[dict] = [
    _node(
        "ai_version",
        "Version",
        "Autoinstall format version (always 1) and the sections the installer still asks about.",
        "version",
        [
            {"key": "version", "label": "Version", "type": "int", "help": "Always 1.", "default": 1},
            {
                "key": "interactive-sections",
                "label": "Interactive sections",
                "type": "string_list",
                "help": "Installer screens still shown to the user; * shows all. PXE installs must stay unattended.",
                "placeholder": "network",
            },
        ],
    ),
    _node(
        "ai_early_commands",
        "Early commands",
        "Shell commands the installer runs before it probes disks and network.",
        "early-commands",
        [
            _commands(
                "early-commands",
                "Early commands",
                "Run in the installer environment, in order.",
                EARLY_MANAGED,
                EARLY_WARNING,
            )
        ],
    ),
    _node(
        "ai_locale",
        "Locale",
        "Installer and installed-system locale.",
        "locale",
        [_str("locale", "Locale", "For example en_US.UTF-8.", placeholder="en_US.UTF-8")],
    ),
    _node(
        "ai_refresh_installer",
        "Refresh installer",
        "Update the installer snap before installing.",
        "refresh-installer",
        [
            _obj(
                "refresh-installer",
                "Refresh installer",
                [
                    _bool("update", "Update", "Refresh the installer to the newest version first.", default=False),
                    _str("channel", "Channel", "Snap channel to refresh from.", placeholder="stable/ubuntu-24.04"),
                ],
            )
        ],
    ),
    _node(
        "ai_keyboard",
        "Keyboard",
        "Keyboard layout for the installed system.",
        "keyboard",
        [
            _obj(
                "keyboard",
                "Keyboard",
                [
                    _str("layout", "Layout", "XKB layout, for example us.", placeholder="us"),
                    _str("variant", "Variant", "XKB variant, for example dvorak."),
                    _str("toggle", "Toggle", "Key combination that switches layouts, for example alt_shift_toggle."),
                ],
            )
        ],
    ),
    _node(
        "ai_source",
        "Source",
        "Which install source to use from the installer media.",
        "source",
        [
            _obj(
                "source",
                "Source",
                [
                    _str(
                        "id",
                        "Source ID",
                        "{{source_id}} uses the image's source (for example ubuntu-server).",
                        placeholder="{{source_id}}",
                    ),
                    _bool("search_drivers", "Search drivers", "Look for third-party drivers.", default=True),
                ],
            )
        ],
    ),
    _node(
        "ai_network",
        "Network",
        "Netplan network config used during the install and copied to the installed system.",
        "network",
        [
            _obj(
                "network",
                "Network",
                [
                    {"key": "version", "label": "Version", "type": "int", "help": "Netplan version, always 2."},
                    _enum("renderer", "Renderer", ["networkd", "NetworkManager"]),
                    {
                        "key": "ethernets",
                        "label": "Ethernets",
                        "type": "row_list",
                        "emit": "mapping",
                        "key_field": "id",
                        "item_fields": _ETHERNET_FIELDS,
                        "preserve_extra": True,
                        "item_label": "interface",
                        "summary_key": "id",
                        "help": "One card per netplan ethernet entry. Other netplan keys go in each card's Other keys.",
                    },
                    _yaml("wifis", "Wi-Fi", "dict", "Netplan wifis mapping."),
                    _yaml("bonds", "Bonds", "dict", "Netplan bonds mapping."),
                    _yaml("bridges", "Bridges", "dict", "Netplan bridges mapping."),
                    _yaml("vlans", "VLANs", "dict", "Netplan vlans mapping."),
                ],
            )
        ],
    ),
    _node(
        "ai_proxy",
        "Proxy",
        "HTTP proxy for the installer and the installed system.",
        "proxy",
        [_str("proxy", "Proxy URL", placeholder="http://proxy.lan:3128")],
    ),
    _node(
        "ai_apt",
        "Apt",
        "Apt mirrors and sources used during the install.",
        "apt",
        [
            _obj(
                "apt",
                "Apt",
                [
                    _bool("preserve_sources_list", "Preserve sources list", default=False),
                    _bool("geoip", "GeoIP mirror", "Pick a nearby country mirror by GeoIP lookup.", default=True),
                    _enum(
                        "fallback",
                        "Fallback",
                        ["abort", "continue-anyway", "offline-install"],
                        "What to do when no mirror works. offline-install installs from the media only.",
                    ),
                    {
                        "key": "disable_components",
                        "label": "Disable components",
                        "type": "enum_list",
                        "choices": ["universe", "multiverse", "restricted", "contrib", "non-free"],
                    },
                    _obj(
                        "mirror-selection",
                        "Mirror selection",
                        [
                            {
                                "key": "primary",
                                "label": "Primary mirrors",
                                "type": "row_list",
                                "item_fields": _MIRROR_FIELDS,
                                "preserve_extra": True,
                                "allow_scalars": True,
                                "item_label": "mirror",
                                "summary_key": "uri",
                                "help": "Tried in order. A plain country-mirror entry is kept as-is.",
                            }
                        ],
                    ),
                    _yaml("primary", "Primary (legacy)", "list", "curtin-style primary mirror list."),
                    _yaml("security", "Security", "list", "curtin-style security mirror list."),
                    _yaml("sources", "Sources", "dict", "Extra apt sources, keyed by file name."),
                ],
            )
        ],
    ),
    _node(
        "ai_storage",
        "Storage",
        "Disk layout for the installed system.",
        "storage",
        [
            _obj(
                "storage",
                "Storage",
                [
                    _obj(
                        "layout",
                        "Layout",
                        [
                            _enum(
                                "name",
                                "Layout",
                                ["lvm", "direct", "zfs"],
                                "lvm uses LVM; direct uses plain partitions.",
                            ),
                            _enum(
                                "sizing-policy",
                                "Sizing policy",
                                ["scaled", "all"],
                                "LVM only: all uses the whole disk for the root volume.",
                            ),
                            _obj(
                                "match",
                                "Disk match",
                                [
                                    _enum("size", "Size", ["largest", "smallest"]),
                                    _bool("ssd", "SSD"),
                                    _str("serial", "Serial", "Glob on the disk serial."),
                                    _str("model", "Model", "Glob on the disk model."),
                                    _str("vendor", "Vendor", "Glob on the disk vendor."),
                                    _str("path", "Path", "Glob on the device path, for example /dev/sda."),
                                    _bool(
                                        "install-media", "Install media", "Match the disk the installer booted from."
                                    ),
                                ],
                            ),
                            _str(
                                "password",
                                "Encryption passphrase",
                                "Encrypts the disk (LVM or ZFS). Placeholders only.",
                                credential=True,
                                placeholder="{{password}}",
                            ),
                            {
                                "key": "reset-partition",
                                "label": "Reset partition",
                                "type": "flex",
                                "choices": ["true", "false"],
                                "help": "true, or a size such as 12G, to add a factory-reset partition.",
                            },
                        ],
                        require_subkey="name",
                    ),
                    _yaml("swap", "Swap", "dict", "For example size: 0 turns off the swap file."),
                    _yaml("grub", "GRUB", "dict", "curtin grub settings."),
                    _yaml(
                        "config", "Config (action list)", "list", "Full curtin storage action list; overrides layout."
                    ),
                ],
            )
        ],
    ),
    _node(
        "ai_identity",
        "Identity",
        "First user and hostname of the installed system.",
        "identity",
        [
            _obj(
                "identity",
                "Identity",
                [
                    _str("realname", "Real name"),
                    _str(
                        "username",
                        "Username",
                        "{{username}} uses the machine's Linux account.",
                        placeholder="{{username}}",
                    ),
                    _str("hostname", "Hostname", "{{hostname}} uses the machine hostname.", placeholder="{{hostname}}"),
                    _str(
                        "password",
                        "Password hash",
                        "Crypted password. Placeholders only; {{password_hash}} is filled from the machine password.",
                        credential=True,
                        placeholder="{{password_hash}}",
                    ),
                ],
            )
        ],
    ),
    _node(
        "ai_active_directory",
        "Active Directory",
        "Join an Active Directory domain. The join password is asked for interactively.",
        "active-directory",
        [
            _obj(
                "active-directory",
                "Active Directory",
                [_str("admin-name", "Admin name"), _str("domain-name", "Domain name", placeholder="ad.example.com")],
            )
        ],
    ),
    _node(
        "ai_ubuntu_pro",
        "Ubuntu Pro",
        "Attach the installed system to Ubuntu Pro.",
        "ubuntu-pro",
        [
            _yaml(
                "ubuntu-pro",
                "Ubuntu Pro",
                "dict",
                "For example token: ... The token is stored in the seed as plain text; anyone who can read seeds sees it.",
            )
        ],
    ),
    _node(
        "ai_ssh",
        "SSH",
        "OpenSSH server on the installed system.",
        "ssh",
        [
            _obj(
                "ssh",
                "SSH",
                [
                    _bool("install-server", "Install OpenSSH server", default=False),
                    _bool("allow-pw", "Allow password login", "Default: true when no keys are given."),
                    {
                        "key": "authorized-keys",
                        "label": "Authorized keys",
                        "type": "string_list",
                        "block_token": "ssh_keys",
                        "help": "Public keys for the first user.",
                    },
                ],
            )
        ],
    ),
    _node(
        "ai_codecs",
        "Codecs",
        "Restricted multimedia codecs.",
        "codecs",
        [_obj("codecs", "Codecs", [_bool("install", "Install codecs", default=False)])],
    ),
    _node(
        "ai_drivers",
        "Drivers",
        "Third-party drivers.",
        "drivers",
        [_obj("drivers", "Drivers", [_bool("install", "Install drivers", default=False)])],
    ),
    _node(
        "ai_oem",
        "OEM",
        "OEM meta-packages.",
        "oem",
        [
            _obj(
                "oem",
                "OEM",
                [
                    {
                        "key": "install",
                        "label": "Install",
                        "type": "flex",
                        "choices": ["auto", "true", "false"],
                        "help": "auto installs OEM packages on Desktop only.",
                    }
                ],
            )
        ],
    ),
    _node(
        "ai_snaps",
        "Snaps",
        "Snaps to install.",
        "snaps",
        [
            {
                "key": "snaps",
                "label": "Snaps",
                "type": "row_list",
                "item_fields": [
                    _str("name", "Name", required=True, placeholder="lxd"),
                    _str("channel", "Channel", placeholder="stable"),
                    _bool("classic", "Classic confinement", default=False),
                ],
                "preserve_extra": True,
                "item_label": "snap",
                "summary_key": "name",
            }
        ],
    ),
    _node(
        "ai_debconf",
        "Debconf selections",
        "debconf-set-selections input applied on the installed system.",
        "debconf-selections",
        [
            {
                "key": "debconf-selections",
                "label": "Debconf selections",
                "type": "text",
                "placeholder": "bind9 bind9/run-resolvconf boolean false",
            }
        ],
    ),
    _node(
        "ai_packages",
        "Packages",
        "Extra packages the installer adds. {{packages}} uses the machine packages field.",
        "packages",
        [
            {
                "key": "packages",
                "label": "Packages",
                "type": "string_list",
                "block_token": "packages",
                "placeholder": "htop",
            }
        ],
    ),
    _node(
        "ai_kernel",
        "Kernel",
        "Kernel package or flavor. Set one, not both.",
        "kernel",
        [
            _obj(
                "kernel",
                "Kernel",
                [
                    _str("package", "Package", placeholder="linux-generic-hwe-24.04"),
                    _str("flavor", "Flavor", placeholder="hwe"),
                ],
            )
        ],
    ),
    _node(
        "ai_kernel_crash_dumps",
        "Kernel crash dumps",
        "kdump on the installed system.",
        "kernel-crash-dumps",
        [
            _obj(
                "kernel-crash-dumps",
                "Kernel crash dumps",
                [
                    {
                        "key": "enabled",
                        "label": "Enabled",
                        "type": "flex",
                        "choices": ["true", "false"],
                        "help": "Empty lets the installer decide.",
                    }
                ],
            )
        ],
    ),
    _node(
        "ai_timezone",
        "Timezone",
        "Installer timezone. home-lab-pxe drops this key when serving; set the timezone in user-data instead.",
        "timezone",
        [_str("timezone", "Timezone", placeholder="Etc/UTC")],
    ),
    _node(
        "ai_updates",
        "Updates",
        "Which updates to install at the end of the install.",
        "updates",
        [_enum("updates", "Updates", ["security", "all"], "security installs security updates only.")],
    ),
    _node(
        "ai_shutdown",
        "Shutdown",
        "What the machine does when the install finishes.",
        "shutdown",
        [_enum("shutdown", "Shutdown", ["reboot", "poweroff"])],
    ),
    _node(
        "ai_reporting",
        "Reporting",
        "Where the installer sends progress events.",
        "reporting",
        [_yaml("reporting", "Reporting", "dict", "For example central: {type: rsyslog, destination: @192.168.1.2}.")],
    ),
    _node(
        "ai_late_commands",
        "Late commands",
        "Shell commands run after the install, before the reboot. The installed system is at /target.",
        "late-commands",
        [
            _commands(
                "late-commands",
                "Late commands",
                "Run in the installer environment, in order.",
                LATE_MANAGED,
                LATE_WARNING,
            )
        ],
    ),
    _node(
        "ai_error_commands",
        "Error commands",
        "Shell commands run when the install fails.",
        "error-commands",
        [
            _commands(
                "error-commands",
                "Error commands",
                "Run in the installer environment, in order.",
                ERROR_MANAGED,
                ERROR_WARNING,
            )
        ],
    ),
]

INSTALLER_FIELDS: dict[str, dict] = {}
INSTALLER_KEY_OWNER: dict[str, dict] = {}
for _node_def in INSTALLER_NODES:
    for _field in _node_def["fields"]:
        INSTALLER_FIELDS.setdefault(_field["key"], _field)
        INSTALLER_KEY_OWNER.setdefault(_field["key"], _node_def)

# ``user-data`` is edited through the cloud-config nodes, never as an installer field.
USER_DATA_KEY = "user-data"


def installer_node_for_key(key: str) -> dict | None:
    return INSTALLER_KEY_OWNER.get(key)
