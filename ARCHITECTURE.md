# Architecture

Visual companion to [`PLAN.md`](PLAN.md). These diagrams describe the **v1 target**, not the current (bootstrap) tree.

## 1. Container

One Compose service on a **Linux host** with `network_mode: host`. `scripts/entrypoint.sh` starts **dnsmasq** (DHCP + TFTP) and **uvicorn** (FastAPI). The web process drops to non-root after bind; dnsmasq keeps `NET_ADMIN` / `NET_RAW` so it can answer DHCP. The container is **not** `privileged: true` unless a later milestone proves it is required.

```mermaid
flowchart TB
  subgraph host["Linux host NIC — PXE_BIND_INTERFACE"]
    LAN["LAN: DHCP / TFTP / HTTP :8080 / HTTPS :8443 / NFS / SMB"]
  end

  subgraph ctr["home-lab-pxe container"]
    EP["entrypoint.sh"]

    subgraph dns["dnsmasq"]
      DHCP["proxyDHCP or authoritative DHCP"]
      TFTP["TFTP: iPXE binaries"]
    end

    subgraph app["uvicorn + FastAPI"]
      UI["Admin UI + session API"]
      IPXE["GET /ipxe/{mac}"]
      SEED["Guest seeds<br/>cloud-init / unattend / Cloudbase-Init"]
      FILES["Kernels, initrd, WIM"]
      POL["src/boot policy"]
    end

    subgraph vol["volume /var/lib/pxe"]
      DATA["data/pxe.db<br/>ciphertext local accounts"]
      IMG["images/"]
      TFTPD["tftp/"]
      SSL["ssl/tls.crt + tls.key"]
    end
  end

  LAN --> DHCP
  LAN --> TFTP
  LAN --> UI
  LAN --> IPXE
  LAN --> SEED
  LAN --> FILES

  EP --> DHCP
  EP --> UI
  IPXE --> POL
  POL --> DATA
  SEED --> DATA
  UI --> DATA
  TFTP --> TFTPD
  FILES --> IMG
  UI --> SSL
```

### 1.1 Processes and privileges

```mermaid
flowchart LR
   subgraph pid1["PID 1 — run-web.sh as uid 10001"]
    H["uvicorn HTTP :8080"]
    S["uvicorn HTTPS :8443"]
    X["extract_worker"]
  end
  subgraph root["entrypoint.sh then exec gosu"]
    E["starts dnsmasq + smbd + ganesha.nfsd + TLS files"]
  end
  E --> D["dnsmasq<br/>caps: NET_ADMIN, NET_RAW<br/>ports 67 / 69"]
  E --> SMB["smbd pxe-media<br/>TCP 445, not published on Docker Desktop"]
  E --> NFS["ganesha.nfsd per-generation casper export<br/>TCP/UDP 111, 2049, 20048"]
  E --> pid1
  H --> F["FastAPI app"]
  S --> F
```

### 1.2 Volumes and env

```mermaid
flowchart TB
  ENV[".env on host"]
  ENV --> SK["SECRET_KEY — sessions"]
  ENV --> EK["ENCRYPTION_KEY — Fernet"]
  ENV --> IF["PXE_BIND_INTERFACE"]
  ENV --> MODE["PXE_DHCP_MODE=proxy|authoritative"]
  ENV --> URL["PXE_PUBLIC_URL"]

  subgraph mounts["bind mounts"]
    V1["/var/lib/pxe/data"]
    V2["/var/lib/pxe/images"]
    V3["/var/lib/pxe/tftp"]
    V4["/var/lib/pxe/ssl"]
  end

  V1 --> DB["SQLite inventory + vault"]
  V2 --> PAY["Ubuntu kernel/initrd, Windows WIMs"]
  V3 --> BIN["ipxe.efi, snponly.efi, undionly.kpxe"]
  V4 --> TLS["self-signed or operator PEM"]
```

### 1.3 DHCP coexistence

```mermaid
flowchart LR
  C["Client PXE firmware"] --> B["DHCP broadcast"]
  B --> R["Existing LAN DHCP<br/>router / Windows DHCP<br/>IP + default gateway"]
  B --> P["home-lab-pxe proxyDHCP<br/>next-server + iPXE filename"]
  R --> C
  P --> C
```

Authoritative mode (`PXE_DHCP_MODE=authoritative`) makes dnsmasq own the address range instead. Default is **proxy** so the home-lab router stays the DHCP server.

When the existing LAN DHCP server must point clients at this box (DHCP disabled here), set option 66 to the host LAN IPv4 and option 67 to `undionly.kpxe` / `ipxe.efi` / `snponly.efi`. Option 60 (`PXEClient`) is required only when that DHCP server and this PXE/TFTP service share the same physical machine. Leave 60/66/67 unset on the other server if this container is already running proxyDHCP.

---

## 2. Boot data plane

Every managed machine is **PXE-first**. “Skip PXE” means **skip the menu**, not “never talk to the server.” Deployed hosts still fetch `/ipxe/{mac}` so a staged job can take the next boot.

```mermaid
sequenceDiagram
  autonumber
  participant FW as Firmware
  participant DHCP as LAN DHCP
  participant PXE as home-lab-pxe
  participant Disk as Local disk

  FW->>DHCP: DHCP discover
  DHCP-->>FW: IP lease
  PXE-->>FW: proxyDHCP: TFTP iPXE
  FW->>PXE: TFTP iPXE binary
  FW->>PXE: GET /ipxe/{mac}
  alt unknown / pending unnamed / disabled
    PXE-->>FW: sleep timeout + exit / sanboot
    FW->>Disk: next boot device
  else named / ready / deployed / timeout_error
    PXE-->>FW: iPXE folder menu (countdown to disk)
  else deploying / staged, Linux
    PXE-->>FW: kernel + initrd + cloud-init URL
  else deploying / staged, Windows
    PXE-->>FW: wimboot + unattend.xml URL
  end
```

### 2.1 Boot-policy decision

```mermaid
flowchart TD
  REQ["GET /ipxe/{mac}"] --> ID{"MAC or UUID<br/>in inventory?"}
  ID -->|no| REG["Insert pending"]
  REG --> DISK["Timeout then next boot device"]
  ID -->|yes| ST{"state"}
  ST --> skip["unnamed pending / disabled"]
  skip --> DISK
  ST --> menu["named / ready / deployed / timeout_error"]
  menu --> FOLDER["iPXE folder menu"]
  ST --> inst["deploying / staged / imaging"]
  inst --> OS{"os_family"}
  OS --> linux["Linux: kernel + nocloud"]
  OS --> win["Windows: wimboot + unattend"]
```

### 2.2 Linux install

```mermaid
sequenceDiagram
  autonumber
  participant IPXE as iPXE
  participant API as FastAPI
  participant Vault as Fernet vault
  participant OS as Installer + cloud-init

  IPXE->>API: GET /ipxe/{mac}
  API-->>IPXE: kernel cmdline ds=nocloud
  IPXE->>API: GET kernel / initrd
  OS->>API: GET /cloud-init/{id}/meta-data
  OS->>API: GET /cloud-init/{id}/user-data
  API->>Vault: decrypt linux_root
  Vault-->>API: username + password in memory
  API-->>OS: rendered user-data (source.id from image catalog)
  OS->>API: phone_home deployed
```

### 2.3 Windows Server install

```mermaid
sequenceDiagram
  autonumber
  participant IPXE as iPXE
  participant API as FastAPI
  participant Vault as Fernet vault
  participant PE as WinPE / Setup
  participant CBI as Cloudbase-Init

  IPXE->>API: GET /ipxe/{mac}
  API-->>IPXE: wimboot + boot.wim / install.wim
  PE->>API: GET /windows/{id}/unattend.xml
  API->>Vault: decrypt windows_administrator
  API-->>PE: unattend.xml (IMAGE NAME/INDEX from catalog; not cached plaintext)
  PE->>CBI: first boot of sysprep’d image
  CBI->>API: GET /cloudbase-init/{id}/
  CBI->>API: callback deployed
```

### 2.4 Machine lifecycle

```mermaid
stateDiagram-v2
  [*] --> pending: first PXE of unknown MAC
  pending --> ready: operator names / tags
  pending --> deploying: Deploy
  ready --> deploying: Deploy
  deploying --> imaging: installer early-command
  staged --> imaging: installer early-command
  imaging --> deployed: installer phone_home
  imaging --> timeout_error: Settings timeout
  timeout_error --> deploying: Deploy
  deploying --> deployed: installer callback
  deployed --> staged: console save / reimage
  pending --> disabled: quarantine
  ready --> disabled: quarantine
  deployed --> disabled: quarantine
  imaging --> disabled: quarantine
  timeout_error --> disabled: quarantine
  disabled --> ready: operator enables
```

Identity: **MAC primary**, SMBIOS UUID secondary. A known UUID with a new MAC (NIC swap) attaches the MAC and keeps the record. **Timeout Error** is wait-only (no guest-init); the operator Deploys again. The timer is Settings → Machines (default 15 minutes). New machines inherit the Settings default IANA timezone.

---

## 3. Web console

Jinja2 admin UI behind session login. Target information architecture:

```mermaid
flowchart TB
  Login["/login"] --> Machines["/machines"]
  Machines --> Detail["/machines/{id}"]
  Machines --> Images["/images"]
  Machines --> Activity["/activity"]
  Machines --> Settings["/settings"]
  Detail --> Deploy["POST deploy"]
  Detail --> Stage["POST stage reimage"]
  Detail --> Creds["POST rotate local account"]
  Images --> Import["register image paths"]
  Settings --> Defaults["lab-wide root / Administrator"]
```

### 3.1 Shell

```mermaid
flowchart TB
  subgraph chrome["Browser — operator on LAN"]
    subgraph bar["Top bar"]
      Brand["home-lab-pxe"]
      Nav["Machines    Images    Boot menu    Files    Activity    Settings"]
      User["operator  Log out"]
    end
    subgraph flash["Flash"]
      Msg["3 machines waiting for action"]
    end
    Body["Page body"]
    bar --> flash --> Body
  end
```

### 3.2 Machines list

Pending rows are visually loud so unknown hardware cannot hide in a deployed fleet.

```mermaid
flowchart TB
  subgraph list["Machines"]
    subgraph pendingBox["Pending — waiting for operator"]
      P1["aa:bb:cc:11:22:33   last seen 8s   unknown   [Open]"]
      P2["aa:bb:cc:44:55:66   last seen 1m   unknown   [Open]"]
    end
    subgraph grid["Inventory"]
      H["Host     State      OS        Image           IP"]
      R1["web1     Deployed   linux     ubuntu-24.04    192.168.1.21"]
      R2["lab-dc   Deploying  windows   ws2022          192.168.1.22"]
      R3["build    Imaging    linux     ubuntu-24.04    192.168.1.23"]
    end
  end
  pendingBox --> grid
```

### 3.3 Machine detail

```mermaid
flowchart TB
  subgraph detail["Machine web1"]
    subgraph ident["Identity"]
      I["MAC  UUID  last IP  last PXE  state: deployed"]
    end
    subgraph actions["Actions"]
      A["Deploy   Stage reimage   Mark deployed   Disable"]
    end
    subgraph guest["Guest init"]
      L["Linux: cloud-init fields + raw overlay"]
      W["Windows: Cloudbase-Init fields + raw overlay"]
    end
    subgraph vault["Local account"]
      U["Username  root   visible"]
      PW["Password  ••••••  write-only  [Rotate]"]
    end
    subgraph events["Recent boots"]
      E["12:01  wait    12:04  install    12:18  local"]
    end
  end
  ident --> actions --> guest --> vault --> events
```

Password fields never round-trip. After save the UI shows **set** vs **not set**, not the secret.

### 3.4 Images and settings

```mermaid
flowchart LR
  subgraph images["Images"]
    Limg["ubuntu-24.04   linux    x86_64   ISO extract + source.id"]
    Wimg["ws2022         windows  x86_64   WIM edition + wim_index"]
  end
  subgraph files["Files"]
    Browser["TFTP / Images / Data volume browser"]
  end
  subgraph settings["Settings"]
    Acc["Imaging default local/root account"]
    Mach["Machines  timeout  timezone"]
    Pxe["PXE  public URL  bind  extra options  next-server hints"]
    Dhcp["DHCP  enable  proxy or authoritative"]
    Tftp["TFTP  enable  tftp-root"]
    Tls["HTTPS  self-signed or uploaded PEM"]
    Smb["Windows SMB  pxe-media password rotate"]
  end
```

### 3.5 Operator: discover → deploy → later reimage

```mermaid
sequenceDiagram
  autonumber
  participant M as New machine
  participant PXE as PXE + API
  participant UI as Console
  participant Op as Operator

  M->>PXE: first iPXE check-in
  PXE-->>M: timeout then next boot device
  Op->>UI: sees pending row, sets hostname
  Op->>UI: hostname, image, root/Admin password
  UI->>PXE: encrypt local account, state=deploying
  M->>PXE: GET /ipxe/{mac}
  PXE-->>M: install script
  M->>PXE: guest-init + phone_home
  PXE-->>UI: state=deployed
  Note over Op,M: later: change hostname or rotate password
  Op->>UI: save → state=staged
  M->>PXE: next reboot PXE
  PXE-->>M: new image + bumped instance-id
```

### 3.6 iPXE folder menu (client screen)

Named hosts see folders from the console **Boot menu** page. Operators can reparent a folder (Root is the top of the tree). Unknown and disabled hosts skip the menu.

```mermaid
flowchart TB
  subgraph menu["iPXE named host"]
    T["Network Installation Options"]
    C["Continue to next boot device"]
    W["Windows"]
    L["Linux"]
    O["Tools"]
  end
  T --> C
  T --> W
  T --> L
  T --> O
```

---

## 4. Security architecture

### 4.1 Trust zones

The data plane is **LAN-trust**. Do not publish DHCP, TFTP, or HTTP-boot to the internet. MAC spoofing can impersonate a host; privileged passwords stay out of iPXE so a neighbor cannot read them from the first-stage script.

```mermaid
flowchart TB
  subgraph wan["Untrusted — WAN / internet"]
    X["No DHCP, TFTP, HTTP-boot, or :8443 exposure"]
  end

  subgraph lan["LAN trust"]
    OP["Operator browser"]
    CLI["PXE clients"]
    SPOOF["Residual: MAC spoof"]
  end

  subgraph box["Container"]
    AUTH["Session + CSRF + rate limit"]
    BOOT["/ipxe/{mac}  unauthenticated by design"]
    SEED["Guest seeds  MAC-keyed, LAN reachable"]
    DB["SQLite ciphertext"]
  end

  wan -.->|blocked by host firewall / bind iface| box
  OP -->|HTTPS :8443 or HTTP :8080| AUTH
  CLI --> BOOT
  CLI --> SEED
  SPOOF -.->|can fetch that MAC's seed| SEED
  AUTH --> DB
  SEED --> DB
```

### 4.2 What is authenticated

```mermaid
flowchart TD
  R["HTTP request"] --> K{"path"}
  K --> L["/login  /static"]
  L --> PUB["Public"]
  K --> A["/machines  /images  /boot-menu  /settings  /activity"]
  A --> S{"valid session + CSRF on POST?"}
  S -->|no| DENY["401 / 403"]
  S -->|yes| OP["Operator actions"]
  K --> B["/ipxe/{mac}"]
  B --> LAN1["No session — boot policy only"]
  K --> G["/cloud-init/{id}  /windows/{id}  /cloudbase-init/{id}"]
  G --> LAN2["No session — only while deploying or staged"]
```

Guest seeds must not require a browser cookie (installers cannot log in). They **must not** appear in iPXE text. Seeds that decrypt the vault are served **only** while the machine is `deploying` or `staged` (404 otherwise). A LAN attacker who spoofs the MAC during that window can still pull that machine’s seed; that is an accepted home-lab residual risk.

### 4.3 Credential vault

Linux **root** and Windows **local Administrator** usernames **and** passwords are Fernet-encrypted. Decrypt only in memory while rendering cloud-init, `unattend.xml`, or Cloudbase-Init user-data.

```mermaid
flowchart LR
  subgraph write["Operator save"]
    FORM["HTML form password"]
    CSRF["CSRF token"]
    FORM --> ENC["Fernet.encrypt"]
    CSRF --> ENC
  end

  EK["ENCRYPTION_KEY"] --> ENC
  ENC --> ROW["LocalAccount<br/>encrypted_username<br/>encrypted_password"]

  subgraph read["Serve guest seed"]
    ROW --> DEC["Fernet.decrypt in RAM"]
    DEC --> YAML["cloud-init / unattend / CBI"]
    YAML --> GUEST["Installer only"]
  end

  EK --> DEC
```

```mermaid
flowchart TB
  subgraph never["Never"]
    N1["iPXE script"]
    N2["Logs / activity HTML"]
    N3["JSON GET after save"]
    N4["Git / .env except ENCRYPTION_KEY"]
    N5["Plaintext SQLite column"]
    N6["StagedJob overlay copy of the password"]
    N7["TLS private key in logs or HTML"]
  end
```

`SECRET_KEY` signs cookies. `ENCRYPTION_KEY` wraps vault rows. Both are required in production. `PXE_ALLOW_INSECURE_DEFAULTS=1` is test-only.

### 4.4 Console request hardening

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant W as FastAPI
  participant RL as Rate limit
  participant DB as SQLite

  B->>W: GET /login
  W-->>B: CSRF cookie + form
  B->>W: POST /login
  W->>RL: check
  alt too many failures
    RL-->>B: 429
  else ok
    W->>DB: verify user
    W-->>B: session HttpOnly cookie
  end
  B->>W: POST /machines/{id}/deploy
  W->>W: session + CSRF
  W->>DB: write ciphertext + state
  W-->>B: redirect  — password not in response
```

### 4.5 Container hardening

```mermaid
flowchart TB
  subgraph yes["Do"]
    Y1["network_mode: host on Linux"]
    Y2["Bind DHCP to PXE_BIND_INTERFACE"]
    Y3["NET_ADMIN / NET_RAW only"]
    Y4["Non-root uvicorn"]
    Y5["Path-safe joins under PXE_IMAGE_ROOT"]
    Y6["Trivy High/Critical on image"]
  end
  subgraph no["Do not"]
    N1["privileged: true by default"]
    N2["Publish 67/69/8080/8443 to the internet"]
    N3["Log user-data or unattend"]
    N4["OpenAPI / debug in production"]
  end
```

### 4.6 Data model (security-relevant)

```mermaid
erDiagram
  USER ||--o{ SESSION : has
  MACHINE ||--o{ LOCAL_ACCOUNT : has
  MACHINE ||--o{ STAGED_JOB : has
  MACHINE ||--o{ BOOT_EVENT : has
  IMAGE ||--o{ MACHINE : assigned
  SETTINGS ||--o{ LOCAL_ACCOUNT : "lab defaults"

  USER {
    string username
    string password_hash
  }
  MACHINE {
    string mac PK
    string uuid
    string state
    string instance_id
  }
  IMAGE {
    string name
    string os_family
  }
  LOCAL_ACCOUNT {
    string kind
    bytes encrypted_username
    bytes encrypted_password
  }
  STAGED_JOB {
    string guest_overlay
  }
  BOOT_EVENT {
    string script_kind
  }
```

`STAGED_JOB.guest_overlay` holds non-secret fields and template references. It does **not** store a second plaintext password.

---

## 5. Control-plane modules

```mermaid
flowchart LR
  R["src/routes"] --> B["src/boot"]
  R --> C["src/cloudinit"]
  R --> W["src/windows"]
  R --> I["src/inventory"]
  B --> I
  C --> S["src/security Fernet"]
  W --> S
  I --> S
  I --> DB["src/db SQLite"]
```

| Module | Responsibility |
|--------|----------------|
| `src/boot/` | Policy + `#!ipxe` |
| `src/cloudinit/` | Linux nocloud |
| `src/windows/` | `unattend.xml` + Cloudbase-Init |
| `src/inventory/` | Machines, states, `LocalAccount`, boot-menu tree |
| `src/install_sources.py` | Ubuntu YAML / Windows WIM install-source catalogs |
| `src/security.py` | Fernet, hashing, cookie flags |

Implementation milestones stay in [`PLAN.md`](PLAN.md).
