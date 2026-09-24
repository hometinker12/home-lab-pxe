"""Generated from cloud-init 26.2. Regenerate with scripts/extract_cloudinit_examples.py."""

from __future__ import annotations

ALIASES: dict[str, str] = {
    "ubuntu_advantage": "ubuntu_pro",
    "ca-certs": "ca_certs",
    "grub-dpkg": "grub_dpkg",
    "remove-defaults": "remove_defaults",
    "activation-key": "activation_key",
    "auto-attach": "auto_attach",
    "service-level": "service_level",
    "add-pool": "add_pool",
    "enable-repo": "enable_repo",
    "disable-repo": "disable_repo",
    "rhsm-baseurl": "rhsm_baseurl",
    "server-hostname": "server_hostname",
    "lock-passwd": "lock_passwd",
    "no-create-home": "no_create_home",
    "no-log-init": "no_log_init",
    "no-user-group": "no_user_group",
    "hashed-passwd": "hashed_passwd",
    "plain-text-passwd": "plain_text_passwd",
    "create-groups": "create_groups",
    "primary-group": "primary_group",
    "selinux-user": "selinux_user",
    "ssh-authorized-keys": "ssh_authorized_keys",
    "ssh-import-id": "ssh_import_id",
    "ssh-redirect-user": "ssh_redirect_user",
}

CHPASSWD_TYPE_CHOICES: list[str] = ["", "hash", "text", "RANDOM"]

PATCHES: dict = {
    "users": [
        {"key": "doas", "label": "Doas", "type": "string_list"},
        {"key": "expiredate", "label": "Expiredate", "type": "string", "default": None},
        {"key": "inactive", "label": "Inactive", "type": "string"},
        {"key": "no_create_home", "label": "No create home", "type": "bool", "default": False},
        {"key": "no_log_init", "label": "No log init", "type": "bool", "default": False},
        {"key": "no_user_group", "label": "No user group", "type": "bool", "default": False},
        {"key": "passwd", "label": "Passwd", "type": "string", "credential": True, "placeholder": "{{password_hash}}"},
        {
            "key": "plain_text_passwd",
            "label": "Plain text passwd",
            "type": "string",
            "credential": True,
            "placeholder": "{{password}}",
        },
        {"key": "create_groups", "label": "Create groups", "type": "bool", "default": True},
        {"key": "selinux_user", "label": "Selinux user", "type": "string"},
        {"key": "snapuser", "label": "Snapuser", "type": "string"},
        {"key": "ssh_import_id", "label": "Ssh import id", "type": "string_list"},
        {"key": "ssh_redirect_user", "label": "Ssh redirect user", "type": "bool", "default": False},
    ],
    "write_files": [
        {
            "key": "source",
            "label": "Source",
            "type": "object",
            "object_fields": [
                {"key": "uri", "label": "Uri", "type": "string", "required": True},
                {"key": "headers", "label": "Headers", "type": "yaml_value"},
            ],
            "preserve_extra": True,
        }
    ],
    "ntp": [
        {
            "key": "config",
            "label": "Config",
            "type": "object",
            "object_fields": [
                {"key": "confpath", "label": "Confpath", "type": "string"},
                {"key": "check_exe", "label": "Check exe", "type": "string"},
                {"key": "packages", "label": "Packages", "type": "yaml_value"},
                {"key": "service_name", "label": "Service name", "type": "string"},
                {"key": "template", "label": "Template", "type": "text"},
            ],
            "preserve_extra": True,
        }
    ],
    "resolv_conf": [],
    "power_state": [],
    "phone_home": [],
    "ssh": [
        {
            "key": "ssh_keys",
            "label": "Host keys",
            "type": "object",
            "object_fields": [
                {"key": "rsa_private", "label": "Rsa private", "type": "text", "secret": True},
                {"key": "rsa_public", "label": "Rsa public", "type": "string"},
                {"key": "rsa_certificate", "label": "Rsa certificate", "type": "string"},
                {"key": "ecdsa_private", "label": "Ecdsa private", "type": "text", "secret": True},
                {"key": "ecdsa_public", "label": "Ecdsa public", "type": "string"},
                {"key": "ecdsa_certificate", "label": "Ecdsa certificate", "type": "string"},
                {"key": "ed25519_private", "label": "Ed25519 private", "type": "text", "secret": True},
                {"key": "ed25519_public", "label": "Ed25519 public", "type": "string"},
                {"key": "ed25519_certificate", "label": "Ed25519 certificate", "type": "string"},
            ],
            "preserve_extra": True,
        },
        {
            "key": "ssh_publish_hostkeys",
            "label": "Ssh publish hostkeys",
            "type": "object",
            "object_fields": [
                {"key": "enabled", "label": "Enabled", "type": "bool", "default": True},
                {"key": "blacklist", "label": "Blacklist", "type": "string_list"},
            ],
            "preserve_extra": True,
        },
        {"key": "authkey_hash", "label": "Authkey hash", "type": "string", "default": "sha256"},
    ],
    "keys_to_console": [
        {"key": "ssh_key_console_blacklist", "label": "Ssh key console blacklist", "type": "string_list"}
    ],
    "yum_repos": [{"key": "yum_repo_dir", "label": "Yum repo dir", "type": "string", "default": "/etc/yum.repos.d"}],
}

GENERATED_NODES: list[dict] = [
    {
        "id": "disk_setup",
        "group": "Disks",
        "label": "Disk setup",
        "help": "Partition tables, device aliases, and filesystems.",
        "fields": [
            {
                "key": "device_aliases",
                "label": "Device aliases",
                "type": "row_list",
                "emit": "mapping_scalar",
                "key_field": "name",
                "item_fields": [
                    {"key": "name", "label": "Name", "type": "string", "required": True},
                    {"key": "value", "label": "Value", "type": "string"},
                ],
            },
            {
                "key": "disk_setup",
                "label": "Disk setup",
                "type": "row_list",
                "emit": "mapping",
                "key_field": "name",
                "item_fields": [
                    {"key": "name", "label": "Key", "type": "string", "required": True},
                    {
                        "key": "table_type",
                        "label": "Table type",
                        "type": "enum",
                        "choices": ["", "mbr", "gpt"],
                        "default": "mbr",
                    },
                    {"key": "layout", "label": "Layout", "type": "disk_layout"},
                    {"key": "overwrite", "label": "Overwrite", "type": "bool", "default": False},
                ],
                "preserve_extra": True,
            },
            {
                "key": "fs_setup",
                "label": "Fs setup",
                "type": "row_list",
                "item_fields": [
                    {"key": "label", "label": "Label", "type": "string"},
                    {"key": "filesystem", "label": "Filesystem", "type": "string"},
                    {"key": "device", "label": "Device", "type": "string"},
                    {"key": "partition", "label": "Partition", "type": "yaml_value"},
                    {"key": "overwrite", "label": "Overwrite", "type": "bool"},
                    {"key": "replace_fs", "label": "Replace fs", "type": "string"},
                    {"key": "extra_opts", "label": "Extra opts", "type": "string_list"},
                    {"key": "cmd", "label": "Cmd", "type": "string_list"},
                ],
                "preserve_extra": True,
            },
        ],
    },
    {
        "id": "mounts",
        "group": "Disks",
        "label": "Mounts",
        "help": "fstab entries and swap files.",
        "fields": [
            {
                "key": "mounts",
                "label": "Mounts",
                "type": "row_list",
                "emit": "tuple",
                "item_fields": [
                    {"key": "fs_spec", "label": "Fs spec", "type": "string"},
                    {"key": "fs_file", "label": "Fs file", "type": "string"},
                    {"key": "fs_vfstype", "label": "Fs vfstype", "type": "string"},
                    {"key": "fs_mntops", "label": "Fs mntops", "type": "string"},
                    {"key": "fs_freq", "label": "Fs freq", "type": "string"},
                    {"key": "fs_passno", "label": "Fs passno", "type": "string"},
                ],
            },
            {"key": "mount_default_fields", "label": "Mount default fields", "type": "string_list"},
            {
                "key": "swap",
                "label": "Swap",
                "type": "object",
                "object_fields": [
                    {"key": "filename", "label": "Filename", "type": "string"},
                    {"key": "size", "label": "Size", "type": "flex"},
                    {"key": "maxsize", "label": "Maxsize", "type": "flex"},
                ],
                "preserve_extra": True,
            },
        ],
    },
    {
        "id": "fan",
        "group": "Network",
        "label": "Fan",
        "help": "Ubuntu Fan networking.",
        "fields": [
            {
                "key": "fan",
                "label": "Fan",
                "type": "object",
                "object_fields": [
                    {"key": "config", "label": "Config", "type": "text", "required": True},
                    {"key": "config_path", "label": "Config path", "type": "string", "default": "/etc/network/fan"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "wireguard",
        "group": "Network",
        "label": "WireGuard",
        "help": "WireGuard interfaces.",
        "fields": [
            {
                "key": "wireguard",
                "label": "Wireguard",
                "type": "object",
                "object_fields": [
                    {
                        "key": "interfaces",
                        "label": "Interfaces",
                        "type": "row_list",
                        "item_fields": [
                            {"key": "name", "label": "Name", "type": "string"},
                            {"key": "config_path", "label": "Config path", "type": "string"},
                            {"key": "content", "label": "Content", "type": "text"},
                        ],
                        "preserve_extra": True,
                        "required": True,
                    },
                    {"key": "readinessprobe", "label": "Readinessprobe", "type": "string_list"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "install_hotplug",
        "group": "Agents and distro",
        "label": "Install hotplug",
        "help": "When to apply network hotplug updates.",
        "fields": [
            {
                "key": "updates",
                "label": "Updates",
                "type": "object",
                "object_fields": [
                    {
                        "key": "network",
                        "label": "Network",
                        "type": "object",
                        "object_fields": [{"key": "when", "label": "When", "type": "yaml_value", "required": True}],
                        "preserve_extra": True,
                    }
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "keyboard",
        "group": "Identity",
        "label": "Keyboard",
        "help": "Keyboard layout. The object is omitted without a layout.",
        "fields": [
            {
                "key": "keyboard",
                "label": "Keyboard",
                "type": "object",
                "object_fields": [
                    {"key": "layout", "label": "Layout", "type": "disk_layout", "required": True},
                    {"key": "model", "label": "Model", "type": "string", "default": "pc105"},
                    {"key": "variant", "label": "Variant", "type": "string"},
                    {"key": "options", "label": "Options", "type": "string"},
                ],
                "preserve_extra": True,
                "require_subkey": "layout",
            }
        ],
    },
    {
        "id": "apt",
        "group": "Packages",
        "label": "Apt",
        "help": "Apt mirrors, sources, and pipelining.",
        "fields": [
            {
                "key": "apt",
                "label": "Apt",
                "type": "object",
                "object_fields": [
                    {
                        "key": "preserve_sources_list",
                        "label": "Preserve sources list",
                        "type": "bool",
                        "default": False,
                    },
                    {"key": "disable_suites", "label": "Disable suites", "type": "string_list"},
                    {
                        "key": "primary",
                        "label": "Primary",
                        "type": "row_list",
                        "item_fields": [
                            {"key": "arches", "label": "Arches", "type": "yaml_value", "required": True},
                            {"key": "uri", "label": "Uri", "type": "string"},
                            {"key": "search", "label": "Search", "type": "yaml_value"},
                            {"key": "search_dns", "label": "Search dns", "type": "bool"},
                            {"key": "keyid", "label": "Keyid", "type": "string"},
                            {"key": "key", "label": "Key", "type": "string"},
                            {"key": "keyserver", "label": "Keyserver", "type": "string"},
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "security",
                        "label": "Security",
                        "type": "row_list",
                        "item_fields": [
                            {"key": "arches", "label": "Arches", "type": "yaml_value", "required": True},
                            {"key": "uri", "label": "Uri", "type": "string"},
                            {"key": "search", "label": "Search", "type": "yaml_value"},
                            {"key": "search_dns", "label": "Search dns", "type": "bool"},
                            {"key": "keyid", "label": "Keyid", "type": "string"},
                            {"key": "key", "label": "Key", "type": "string"},
                            {"key": "keyserver", "label": "Keyserver", "type": "string"},
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "add_apt_repo_match",
                        "label": "Add apt repo match",
                        "type": "string",
                        "default": "^[\\w-]+:\\w",
                    },
                    {
                        "key": "debconf_selections",
                        "label": "Debconf selections",
                        "type": "row_list",
                        "emit": "mapping_scalar",
                        "key_field": "name",
                        "item_fields": [
                            {"key": "name", "label": "Name", "type": "string", "required": True},
                            {"key": "value", "label": "Value", "type": "string"},
                        ],
                    },
                    {"key": "sources_list", "label": "Sources list", "type": "text"},
                    {"key": "conf", "label": "Conf", "type": "text"},
                    {"key": "https_proxy", "label": "Https proxy", "type": "string"},
                    {"key": "http_proxy", "label": "Http proxy", "type": "string"},
                    {"key": "proxy", "label": "Proxy", "type": "string"},
                    {"key": "ftp_proxy", "label": "Ftp proxy", "type": "string"},
                    {
                        "key": "sources",
                        "label": "Sources",
                        "type": "row_list",
                        "emit": "mapping",
                        "key_field": "name",
                        "item_fields": [
                            {"key": "name", "label": "Key", "type": "string", "required": True},
                            {"key": "value", "label": "Value", "type": "yaml_value"},
                        ],
                        "preserve_extra": True,
                    },
                ],
                "preserve_extra": True,
            },
            {"key": "apt_pipelining", "label": "Apt pipelining", "type": "flex"},
        ],
    },
    {
        "id": "snap",
        "group": "Packages",
        "label": "Snap",
        "help": "Snap assertions and commands.",
        "fields": [
            {
                "key": "snap",
                "label": "Snap",
                "type": "object",
                "object_fields": [
                    {
                        "key": "assertions",
                        "label": "Assertions",
                        "type": "row_list",
                        "emit": "list_or_map",
                        "key_field": "name",
                        "item_fields": [
                            {"key": "name", "label": "Name", "type": "string"},
                            {"key": "value", "label": "Value", "type": "text"},
                        ],
                    },
                    {
                        "key": "commands",
                        "label": "Commands",
                        "type": "row_list",
                        "emit": "list_or_map",
                        "key_field": "name",
                        "item_fields": [
                            {"key": "name", "label": "Name", "type": "string"},
                            {"key": "value", "label": "Value", "type": "text"},
                        ],
                    },
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "apk_repos",
        "group": "Packages",
        "label": "APK",
        "help": "Alpine APK repositories.",
        "fields": [
            {
                "key": "apk_repos",
                "label": "Apk repos",
                "type": "object",
                "object_fields": [
                    {
                        "key": "preserve_repositories",
                        "label": "Preserve repositories",
                        "type": "bool",
                        "default": False,
                    },
                    {
                        "key": "alpine_repo",
                        "label": "Alpine repo",
                        "type": "object",
                        "object_fields": [
                            {
                                "key": "base_url",
                                "label": "Base url",
                                "type": "string",
                                "default": "https://dl-cdn.alpinelinux.org/alpine",
                            },
                            {
                                "key": "community_enabled",
                                "label": "Community enabled",
                                "type": "bool",
                                "default": False,
                            },
                            {"key": "testing_enabled", "label": "Testing enabled", "type": "bool", "default": False},
                            {"key": "version", "label": "Version", "type": "string", "required": True},
                        ],
                        "preserve_extra": True,
                    },
                    {"key": "local_repo_base_url", "label": "Local repo base url", "type": "string"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "yum_repos",
        "group": "Packages",
        "label": "Yum repos",
        "help": "Yum repository files.",
        "fields": [
            {
                "key": "yum_repos",
                "label": "Yum repos",
                "type": "row_list",
                "emit": "mapping",
                "key_field": "_key",
                "item_fields": [
                    {"key": "_key", "label": "Key", "type": "string", "required": True},
                    {"key": "baseurl", "label": "Baseurl", "type": "string"},
                    {"key": "metalink", "label": "Metalink", "type": "string"},
                    {"key": "mirrorlist", "label": "Mirrorlist", "type": "string"},
                    {"key": "name", "label": "Name", "type": "string"},
                    {"key": "enabled", "label": "Enabled", "type": "bool", "default": True},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "zypper",
        "group": "Packages",
        "label": "Zypper",
        "help": "Zypper repositories.",
        "fields": [
            {
                "key": "zypper",
                "label": "Zypper",
                "type": "object",
                "object_fields": [
                    {
                        "key": "repos",
                        "label": "Repos",
                        "type": "row_list",
                        "item_fields": [
                            {"key": "id", "label": "Id", "type": "string", "required": True},
                            {"key": "baseurl", "label": "Baseurl", "type": "string", "required": True},
                        ],
                        "preserve_extra": True,
                    },
                    {"key": "config", "label": "Config", "type": "lines", "parser": "options"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "ca_certs",
        "group": "Agents and distro",
        "label": "CA certificates",
        "help": "Trusted CA certificates.",
        "fields": [
            {
                "key": "ca_certs",
                "label": "Ca certs",
                "type": "object",
                "object_fields": [
                    {"key": "remove_defaults", "label": "Remove defaults", "type": "bool", "default": False},
                    {"key": "trusted", "label": "Trusted", "type": "string_list"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "rsyslog",
        "group": "Agents and distro",
        "label": "Rsyslog",
        "help": "Rsyslog configuration.",
        "fields": [
            {
                "key": "rsyslog",
                "label": "Rsyslog",
                "type": "object",
                "object_fields": [
                    {"key": "config_dir", "label": "Config dir", "type": "string"},
                    {"key": "config_filename", "label": "Config filename", "type": "string"},
                    {"key": "configs", "label": "Configs", "type": "string_list"},
                    {"key": "remotes", "label": "Remotes", "type": "lines", "parser": "options"},
                    {"key": "service_reload_command", "label": "Service reload command", "type": "yaml_value"},
                    {"key": "install_rsyslog", "label": "Install rsyslog", "type": "bool", "default": False},
                    {"key": "check_exe", "label": "Check exe", "type": "string"},
                    {"key": "packages", "label": "Packages", "type": "string_list", "block_token": "packages"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "ansible",
        "group": "Agents and distro",
        "label": "Ansible",
        "help": "Ansible install and pull.",
        "fields": [
            {
                "key": "ansible",
                "label": "Ansible",
                "type": "object",
                "object_fields": [
                    {
                        "key": "install_method",
                        "label": "Install method",
                        "type": "enum",
                        "choices": ["", "distro", "pip"],
                        "default": "distro",
                    },
                    {"key": "run_user", "label": "Run user", "type": "string"},
                    {"key": "ansible_config", "label": "Ansible config", "type": "string"},
                    {
                        "key": "setup_controller",
                        "label": "Setup controller",
                        "type": "object",
                        "object_fields": [
                            {"key": "repositories", "label": "Repositories", "type": "yaml_value"},
                            {"key": "run_ansible", "label": "Run ansible", "type": "yaml_value"},
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "galaxy",
                        "label": "Galaxy",
                        "type": "object",
                        "object_fields": [
                            {"key": "actions", "label": "Actions", "type": "yaml_value", "required": True}
                        ],
                        "preserve_extra": True,
                    },
                    {"key": "package_name", "label": "Package name", "type": "string", "default": "ansible"},
                    {"key": "pull", "label": "Pull", "type": "yaml_value"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "puppet",
        "group": "Agents and distro",
        "label": "Puppet",
        "help": "Puppet agent configuration.",
        "fields": [
            {
                "key": "puppet",
                "label": "Puppet",
                "type": "object",
                "object_fields": [
                    {"key": "install", "label": "Install", "type": "bool", "default": True},
                    {"key": "version", "label": "Version", "type": "string"},
                    {
                        "key": "install_type",
                        "label": "Install type",
                        "type": "enum",
                        "choices": ["", "packages", "aio"],
                        "default": "packages",
                    },
                    {"key": "collection", "label": "Collection", "type": "string"},
                    {"key": "aio_install_url", "label": "Aio install url", "type": "string"},
                    {"key": "cleanup", "label": "Cleanup", "type": "bool", "default": True},
                    {"key": "conf_file", "label": "Conf file", "type": "string"},
                    {"key": "ssl_dir", "label": "Ssl dir", "type": "string"},
                    {"key": "csr_attributes_path", "label": "Csr attributes path", "type": "string"},
                    {"key": "package_name", "label": "Package name", "type": "string"},
                    {"key": "exec", "label": "Exec", "type": "bool", "default": False},
                    {"key": "exec_args", "label": "Exec args", "type": "string_list"},
                    {"key": "start_service", "label": "Start service", "type": "bool", "default": True},
                    {
                        "key": "conf",
                        "label": "Conf",
                        "type": "object",
                        "object_fields": [
                            {"key": "main", "label": "Main", "type": "yaml_value"},
                            {"key": "server", "label": "Server", "type": "yaml_value"},
                            {"key": "agent", "label": "Agent", "type": "yaml_value"},
                            {"key": "user", "label": "User", "type": "yaml_value"},
                            {"key": "ca_cert", "label": "Ca cert", "type": "text"},
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "csr_attributes",
                        "label": "Csr attributes",
                        "type": "object",
                        "object_fields": [
                            {"key": "custom_attributes", "label": "Custom attributes", "type": "yaml_value"},
                            {"key": "extension_requests", "label": "Extension requests", "type": "yaml_value"},
                        ],
                        "preserve_extra": True,
                    },
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "chef",
        "group": "Agents and distro",
        "label": "Chef",
        "help": "Chef client configuration.",
        "fields": [
            {
                "key": "chef",
                "label": "Chef",
                "type": "object",
                "object_fields": [
                    {"key": "directories", "label": "Directories", "type": "string_list"},
                    {"key": "config_path", "label": "Config path", "type": "string", "default": "/etc/chef/client.rb"},
                    {"key": "validation_cert", "label": "Validation cert", "type": "text", "secret": True},
                    {
                        "key": "validation_key",
                        "label": "Validation key",
                        "type": "string",
                        "default": "/etc/chef/validation.pem",
                    },
                    {
                        "key": "firstboot_path",
                        "label": "Firstboot path",
                        "type": "string",
                        "default": "/etc/chef/firstboot.json",
                    },
                    {"key": "exec", "label": "Exec", "type": "bool", "default": False},
                    {"key": "client_key", "label": "Client key", "type": "string", "default": "/etc/chef/client.pem"},
                    {
                        "key": "encrypted_data_bag_secret",
                        "label": "Encrypted data bag secret",
                        "type": "string",
                        "default": None,
                    },
                    {"key": "environment", "label": "Environment", "type": "string", "default": "_default"},
                    {
                        "key": "file_backup_path",
                        "label": "File backup path",
                        "type": "string",
                        "default": "/var/chef/backup",
                    },
                    {
                        "key": "file_cache_path",
                        "label": "File cache path",
                        "type": "string",
                        "default": "/var/chef/cache",
                    },
                    {
                        "key": "json_attribs",
                        "label": "Json attribs",
                        "type": "string",
                        "default": "/etc/chef/firstboot.json",
                    },
                    {"key": "log_level", "label": "Log level", "type": "string", "default": ":info"},
                    {
                        "key": "log_location",
                        "label": "Log location",
                        "type": "string",
                        "default": "/var/log/chef/client.log",
                    },
                    {"key": "node_name", "label": "Node name", "type": "string"},
                    {
                        "key": "omnibus_url",
                        "label": "Omnibus url",
                        "type": "string",
                        "default": "https://www.chef.io/chef/install.sh",
                    },
                    {"key": "omnibus_url_retries", "label": "Omnibus url retries", "type": "int"},
                    {"key": "omnibus_version", "label": "Omnibus version", "type": "string"},
                    {"key": "pid_file", "label": "Pid file", "type": "string", "default": "/var/run/chef/client.pid"},
                    {"key": "server_url", "label": "Server url", "type": "string"},
                    {"key": "show_time", "label": "Show time", "type": "bool", "default": True},
                    {"key": "ssl_verify_mode", "label": "Ssl verify mode", "type": "string", "default": ":verify_none"},
                    {"key": "validation_name", "label": "Validation name", "type": "string"},
                    {"key": "force_install", "label": "Force install", "type": "bool", "default": False},
                    {"key": "initial_attributes", "label": "Initial attributes", "type": "lines", "parser": "options"},
                    {
                        "key": "install_type",
                        "label": "Install type",
                        "type": "enum",
                        "choices": ["", "packages", "gems", "omnibus"],
                        "default": "packages",
                    },
                    {"key": "run_list", "label": "Run list", "type": "string_list"},
                    {
                        "key": "chef_license",
                        "label": "Chef license",
                        "type": "enum",
                        "choices": ["", "accept", "accept-silent", "accept-no-persist"],
                    },
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "salt_minion",
        "group": "Agents and distro",
        "label": "Salt",
        "help": "Salt minion configuration.",
        "fields": [
            {
                "key": "salt_minion",
                "label": "Salt minion",
                "type": "object",
                "object_fields": [
                    {"key": "pkg_name", "label": "Pkg name", "type": "string"},
                    {"key": "service_name", "label": "Service name", "type": "string"},
                    {"key": "config_dir", "label": "Config dir", "type": "string"},
                    {"key": "conf", "label": "Conf", "type": "lines", "parser": "options"},
                    {"key": "grains", "label": "Grains", "type": "lines", "parser": "options"},
                    {"key": "public_key", "label": "Public key", "type": "string"},
                    {"key": "private_key", "label": "Private key", "type": "string"},
                    {"key": "pki_dir", "label": "Pki dir", "type": "string"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "mcollective",
        "group": "Agents and distro",
        "label": "MCollective",
        "help": "MCollective server configuration.",
        "fields": [
            {
                "key": "mcollective",
                "label": "Mcollective",
                "type": "object",
                "object_fields": [
                    {
                        "key": "conf",
                        "label": "Conf",
                        "type": "object",
                        "object_fields": [
                            {"key": "public-cert", "label": "Public cert", "type": "string"},
                            {"key": "private-cert", "label": "Private cert", "type": "string"},
                        ],
                        "preserve_extra": True,
                    }
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "landscape",
        "group": "Agents and distro",
        "label": "Landscape",
        "help": "Landscape client registration.",
        "fields": [
            {
                "key": "landscape",
                "label": "Landscape",
                "type": "object",
                "object_fields": [
                    {
                        "key": "client",
                        "label": "Client",
                        "type": "object",
                        "object_fields": [
                            {
                                "key": "url",
                                "label": "Url",
                                "type": "string",
                                "default": "https://landscape.canonical.com/message-system",
                            },
                            {
                                "key": "ping_url",
                                "label": "Ping url",
                                "type": "string",
                                "default": "https://landscape.canonical.com/ping",
                            },
                            {
                                "key": "data_path",
                                "label": "Data path",
                                "type": "string",
                                "default": "/var/lib/landscape/client",
                            },
                            {
                                "key": "log_level",
                                "label": "Log level",
                                "type": "enum",
                                "choices": ["", "debug", "info", "warning", "error", "critical"],
                                "default": "info",
                            },
                            {"key": "computer_title", "label": "Computer title", "type": "string", "required": True},
                            {"key": "account_name", "label": "Account name", "type": "string", "required": True},
                            {"key": "registration_key", "label": "Registration key", "type": "string", "secret": True},
                            {"key": "tags", "label": "Tags", "type": "string"},
                            {"key": "http_proxy", "label": "Http proxy", "type": "string"},
                            {"key": "https_proxy", "label": "Https proxy", "type": "string"},
                        ],
                        "preserve_extra": True,
                        "required": True,
                    }
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "ubuntu_pro",
        "group": "Agents and distro",
        "label": "Ubuntu Pro",
        "help": "Ubuntu Pro attach token and services.",
        "fields": [
            {
                "key": "ubuntu_pro",
                "label": "Ubuntu pro",
                "type": "object",
                "object_fields": [
                    {"key": "enable", "label": "Enable", "type": "string_list"},
                    {"key": "enable_beta", "label": "Enable beta", "type": "string_list"},
                    {"key": "token", "label": "Token", "type": "string", "secret": True},
                    {
                        "key": "features",
                        "label": "Features",
                        "type": "object",
                        "object_fields": [
                            {
                                "key": "disable_auto_attach",
                                "label": "Disable auto attach",
                                "type": "bool",
                                "default": False,
                            }
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "config",
                        "label": "Config",
                        "type": "object",
                        "object_fields": [
                            {"key": "http_proxy", "label": "Http proxy", "type": "string"},
                            {"key": "https_proxy", "label": "Https proxy", "type": "string"},
                            {"key": "global_apt_http_proxy", "label": "Global apt http proxy", "type": "string"},
                            {"key": "global_apt_https_proxy", "label": "Global apt https proxy", "type": "string"},
                            {"key": "ua_apt_http_proxy", "label": "Ua apt http proxy", "type": "string"},
                            {"key": "ua_apt_https_proxy", "label": "Ua apt https proxy", "type": "string"},
                        ],
                        "preserve_extra": True,
                    },
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "byobu",
        "group": "Agents and distro",
        "label": "Byobu",
        "help": "Byobu on login.",
        "fields": [
            {
                "key": "byobu_by_default",
                "label": "Byobu by default",
                "type": "enum",
                "choices": [
                    "",
                    "enable-system",
                    "enable-user",
                    "disable-system",
                    "disable-user",
                    "enable",
                    "disable",
                    "user",
                    "system",
                ],
            }
        ],
    },
    {
        "id": "lxd",
        "group": "Agents and distro",
        "label": "LXD",
        "help": "LXD init. Preseed is opaque YAML passed to lxd init.",
        "fields": [
            {
                "key": "lxd",
                "label": "Lxd",
                "type": "object",
                "object_fields": [
                    {
                        "key": "init",
                        "label": "Init",
                        "type": "object",
                        "object_fields": [
                            {"key": "network_address", "label": "Network address", "type": "string"},
                            {"key": "network_port", "label": "Network port", "type": "int"},
                            {
                                "key": "storage_backend",
                                "label": "Storage backend",
                                "type": "enum",
                                "choices": ["", "zfs", "dir", "lvm", "btrfs"],
                                "default": "dir",
                            },
                            {"key": "storage_create_device", "label": "Storage create device", "type": "string"},
                            {"key": "storage_create_loop", "label": "Storage create loop", "type": "int"},
                            {"key": "storage_pool", "label": "Storage pool", "type": "string"},
                            {"key": "trust_password", "label": "Trust password", "type": "string", "secret": True},
                        ],
                        "preserve_extra": True,
                    },
                    {
                        "key": "bridge",
                        "label": "Bridge",
                        "type": "object",
                        "object_fields": [
                            {
                                "key": "mode",
                                "label": "Mode",
                                "type": "enum",
                                "choices": ["", "none", "existing", "new"],
                                "required": True,
                            },
                            {"key": "name", "label": "Name", "type": "string", "default": "lxdbr0"},
                            {"key": "mtu", "label": "Mtu", "type": "int"},
                            {"key": "ipv4_address", "label": "Ipv4 address", "type": "string"},
                            {"key": "ipv4_netmask", "label": "Ipv4 netmask", "type": "int"},
                            {"key": "ipv4_dhcp_first", "label": "Ipv4 dhcp first", "type": "string"},
                            {"key": "ipv4_dhcp_last", "label": "Ipv4 dhcp last", "type": "string"},
                            {"key": "ipv4_dhcp_leases", "label": "Ipv4 dhcp leases", "type": "int"},
                            {"key": "ipv4_nat", "label": "Ipv4 nat", "type": "bool", "default": False},
                            {"key": "ipv6_address", "label": "Ipv6 address", "type": "string"},
                            {"key": "ipv6_netmask", "label": "Ipv6 netmask", "type": "int"},
                            {"key": "ipv6_nat", "label": "Ipv6 nat", "type": "bool", "default": False},
                            {"key": "domain", "label": "Domain", "type": "string"},
                        ],
                        "preserve_extra": True,
                    },
                    {"key": "preseed", "label": "Preseed", "type": "text"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "drivers",
        "group": "Agents and distro",
        "label": "Ubuntu drivers",
        "help": "Ubuntu driver packages.",
        "fields": [
            {
                "key": "drivers",
                "label": "Drivers",
                "type": "object",
                "object_fields": [
                    {
                        "key": "nvidia",
                        "label": "Nvidia",
                        "type": "object",
                        "object_fields": [
                            {"key": "license-accepted", "label": "License accepted", "type": "bool", "required": True},
                            {"key": "version", "label": "Version", "type": "string"},
                        ],
                        "preserve_extra": True,
                    }
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "grub_dpkg",
        "group": "Agents and distro",
        "label": "GRUB dpkg",
        "help": "GRUB install device debconf settings.",
        "fields": [
            {
                "key": "grub_dpkg",
                "label": "Grub dpkg",
                "type": "object",
                "object_fields": [
                    {"key": "enabled", "label": "Enabled", "type": "bool", "default": False},
                    {"key": "grub-pc/install_devices", "label": "Grub pc/install devices", "type": "string"},
                    {"key": "grub-pc/install_devices_empty", "label": "Grub pc/install devices empty", "type": "flex"},
                    {"key": "grub-efi/install_devices", "label": "Grub efi/install devices", "type": "string"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "rh_subscription",
        "group": "Agents and distro",
        "label": "Red Hat subscription",
        "help": "Red Hat subscription-manager.",
        "fields": [
            {
                "key": "rh_subscription",
                "label": "Rh subscription",
                "type": "object",
                "object_fields": [
                    {"key": "username", "label": "Username", "type": "string"},
                    {
                        "key": "password",
                        "label": "Password",
                        "type": "string",
                        "credential": True,
                        "placeholder": "{{password}}",
                    },
                    {"key": "activation_key", "label": "Activation key", "type": "string", "secret": True},
                    {"key": "org", "label": "Org", "type": "flex"},
                    {"key": "auto_attach", "label": "Auto attach", "type": "bool"},
                    {"key": "service_level", "label": "Service level", "type": "string"},
                    {"key": "add_pool", "label": "Add pool", "type": "string_list"},
                    {"key": "enable_repo", "label": "Enable repo", "type": "string_list"},
                    {"key": "disable_repo", "label": "Disable repo", "type": "string_list"},
                    {"key": "release_version", "label": "Release version", "type": "string"},
                    {"key": "rhsm_baseurl", "label": "Rhsm baseurl", "type": "string"},
                    {"key": "server_hostname", "label": "Server hostname", "type": "string"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "spacewalk",
        "group": "Agents and distro",
        "label": "Spacewalk",
        "help": "Spacewalk registration.",
        "fields": [
            {
                "key": "spacewalk",
                "label": "Spacewalk",
                "type": "object",
                "object_fields": [
                    {"key": "server", "label": "Server", "type": "string"},
                    {"key": "proxy", "label": "Proxy", "type": "string"},
                    {"key": "activation_key", "label": "Activation key", "type": "string", "secret": True},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "rpi",
        "group": "Agents and distro",
        "label": "Raspberry Pi",
        "help": "Raspberry Pi interfaces.",
        "fields": [
            {
                "key": "rpi",
                "label": "Rpi",
                "type": "object",
                "object_fields": [
                    {
                        "key": "interfaces",
                        "label": "Interfaces",
                        "type": "object",
                        "object_fields": [
                            {"key": "spi", "label": "Spi", "type": "bool", "default": False},
                            {"key": "i2c", "label": "I2c", "type": "bool", "default": False},
                            {"key": "serial", "label": "Serial", "type": "yaml_value"},
                            {"key": "onewire", "label": "Onewire", "type": "bool", "default": False},
                        ],
                        "preserve_extra": True,
                    },
                    {"key": "enable_usb_gadget", "label": "Enable usb gadget", "type": "bool", "default": False},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "random_seed",
        "group": "Agents and distro",
        "label": "Seed random",
        "help": "Kernel random seed data.",
        "fields": [
            {
                "key": "random_seed",
                "label": "Random seed",
                "type": "object",
                "object_fields": [
                    {"key": "file", "label": "File", "type": "string", "default": "/dev/urandom"},
                    {"key": "data", "label": "Data", "type": "string"},
                    {
                        "key": "encoding",
                        "label": "Encoding",
                        "type": "enum",
                        "choices": ["", "raw", "base64", "b64", "gzip", "gz"],
                        "default": "raw",
                    },
                    {"key": "command", "label": "Command", "type": "string_list"},
                    {"key": "command_required", "label": "Command required", "type": "bool", "default": False},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "vendor_data",
        "group": "Files and commands",
        "label": "Vendor data",
        "help": "Whether vendor scripts run.",
        "fields": [
            {
                "key": "vendor_data",
                "label": "Vendor data",
                "type": "object",
                "object_fields": [
                    {"key": "enabled", "label": "Enabled", "type": "flex"},
                    {"key": "prefix", "label": "Prefix", "type": "string_list"},
                ],
                "preserve_extra": True,
            }
        ],
    },
    {
        "id": "default_user",
        "group": "Users and SSH",
        "label": "Default user",
        "help": "Override the default user object.",
        "fields": [{"key": "user", "label": "User", "type": "yaml_value"}],
    },
]
